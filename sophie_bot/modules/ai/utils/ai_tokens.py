from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from threading import Lock
from typing import ClassVar

import tiktoken
from pydantic_ai.messages import (
    BinaryContent,
    CachePoint,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from tokenizers import Tokenizer

from sophie_bot.utils.ai_tokenizer_assets import GLM_TOKENIZER_FILENAME, TOKENIZER_DIRECTORY, load_encoding

_TOKENIZER_LOAD_LOCK = Lock()

# Application policy for unrecognized tokenizers, not a claimed provider capability.
# Bound exposure to cl100k estimation error even for a large registered context window.
# Known tokenizers use their full registered capacity instead.
UNKNOWN_TOKENIZER_CONTEXT_CAP = 8192
UNKNOWN_TOKENIZER_INPUT_PERCENT = 80
TOOL_SCHEMA_TOKEN_RESERVE = 4096


@dataclass(frozen=True, slots=True)
class TokenCounter:
    """Local text token counts plus conservative, provider-dependent framing/media allowances.

    Unknown models use the OpenAI cl100k_base tokenizer as an estimate. Tokenizer failures
    are configuration errors, not a reason to silently switch counting strategies. No provider usage is claimed
    to be exact: chat templates, images and hidden provider instructions can differ.
    """

    model_name: str
    tokenizer: tiktoken.Encoding | Tokenizer | None = None
    IMAGE_TOKEN_RESERVE: ClassVar[int] = 4096
    MESSAGE_TOKEN_RESERVE: ClassVar[int] = 16
    PART_TOKEN_RESERVE: ClassVar[int] = 8

    def count_text(self, text: str) -> int:
        if isinstance(self.tokenizer, tiktoken.Encoding):
            return len(self.tokenizer.encode_ordinary(text))
        if isinstance(self.tokenizer, Tokenizer):
            return len(self.tokenizer.encode(text, add_special_tokens=False).ids)
        return len(load_encoding("cl100k_base").encode_ordinary(text))

    def count_content(self, content: str | Sequence[UserContent]) -> int:
        if isinstance(content, str):
            return self.count_text(content)
        total = 0
        for item in content:
            if isinstance(item, str):
                total += self.count_text(item)
            elif isinstance(item, TextContent):
                total += self.count_text(item.content)
            elif isinstance(item, ImageUrl):
                total += self.IMAGE_TOKEN_RESERVE
            elif isinstance(item, BinaryContent):
                total += (
                    self.IMAGE_TOKEN_RESERVE
                    if item.media_type.startswith("image/")
                    else max(self.IMAGE_TOKEN_RESERVE, len(item.data))
                )
            elif not isinstance(item, CachePoint):
                raise TypeError(f"Unsupported modern context media: {type(item).__name__}")
            total += self.PART_TOKEN_RESERVE
        return total

    def count_messages(self, messages: Sequence[ModelRequest | ModelResponse]) -> int:
        """Count model-facing content, never timestamps, usage, IDs of runs, or storage JSON."""
        total = 0
        for message in messages:
            total += self.MESSAGE_TOKEN_RESERVE
            # Instructions are supplied once by the current agent, not replayed per stored request.
            for part in message.parts:
                total += self.PART_TOKEN_RESERVE
                if isinstance(part, UserPromptPart):
                    total += self.count_content(part.content)
                elif isinstance(part, (TextPart, ThinkingPart, SystemPromptPart)):
                    total += self.count_text(part.content)
                elif isinstance(part, ToolCallPart):
                    total += self.count_text(part.tool_name)
                    total += self.count_text(part.tool_call_id)
                    total += self.count_text(part.args_as_json_str())
                elif isinstance(part, ToolReturnPart):
                    total += self.count_text(part.tool_name)
                    total += self.count_text(part.tool_call_id)
                    total += self.count_text(part.model_response_str())
                    total += self.count_content(part.files)
                elif isinstance(part, RetryPromptPart):
                    total += self.count_text(part.tool_name or "")
                    total += self.count_text(part.tool_call_id)
                    total += self.count_text(part.model_response())
                else:
                    raise TypeError(f"Unsupported modern context message part: {type(part).__name__}")
        return total


@dataclass(frozen=True, slots=True)
class ModelTokenBudget:
    counter: TokenCounter
    input_tokens: int
    context_window_tokens: int | None = None

    @classmethod
    def for_model(
        cls, counter: TokenCounter, context_window: int, configured_limit: int, output_tokens: int
    ) -> ModelTokenBudget:
        if configured_limit < 0:
            raise ValueError("Modern context input cap cannot be negative")
        capacity = min(context_window, UNKNOWN_TOKENIZER_CONTEXT_CAP) if counter.tokenizer is None else context_window
        available = max(0, capacity - output_tokens - TOOL_SCHEMA_TOKEN_RESERVE)
        if counter.tokenizer is None:
            available = available * UNKNOWN_TOKENIZER_INPUT_PERCENT // 100
        return cls(counter, min(configured_limit, available) if configured_limit else available, context_window)


@cache
def _load_token_counter(model_name: str) -> TokenCounter:
    name = model_name.lower().removeprefix("openai/")
    if name.rsplit("/", 1)[-1].split(":", 1)[0] in {"glm-5.3", "glm-5.3-flash"}:
        path = (TOKENIZER_DIRECTORY / GLM_TOKENIZER_FILENAME).resolve(strict=True)
        tokenizer = Tokenizer.from_file(str(path))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return TokenCounter(model_name, tokenizer)
    try:
        encoding_name = tiktoken.encoding_name_for_model(name)
    except KeyError:
        load_encoding("cl100k_base")
        return TokenCounter(model_name)
    return TokenCounter(model_name, load_encoding(encoding_name))


def _load_token_counter_serialized(model_name: str) -> TokenCounter:
    # functools.cache alone can execute a cold load more than once across worker threads.
    with _TOKENIZER_LOAD_LOCK:
        return _load_token_counter(model_name)


async def load_token_counter(model_name: str) -> TokenCounter:
    """Load bundled vocabulary off-loop, without network access or writable runtime caches.

    Missing or invalid known-model assets fail the request; run the image-build bundler
    when running from a source checkout.
    """
    return await asyncio.to_thread(_load_token_counter_serialized, model_name)
