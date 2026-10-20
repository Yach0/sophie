from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider
from tokenizers import Tokenizer

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions, _candidate_run_kwargs
from sophie_bot.modules.ai.utils.ai_tokens import (
    TOOL_SCHEMA_TOKEN_RESERVE,
    UNKNOWN_TOKENIZER_CONTEXT_CAP,
    UNKNOWN_TOKENIZER_INPUT_PERCENT,
    ModelTokenBudget,
    TokenCounter,
    load_token_counter,
)
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.utils.ai_tokenizer_assets import load_encoding
from sophie_bot.utils.feature_flags import get_default_value
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401


async def test_unknown_uses_openai_tokens_and_conservative_capacity() -> None:
    counter = await load_token_counter("unregistered/model")
    text = "你好 world " * 100
    assert counter.count_text(text) == len(load_encoding("cl100k_base").encode_ordinary(text))
    budget = ModelTokenBudget.for_model(counter, 200000, 0, 2048)
    assert budget.input_tokens == (
        (UNKNOWN_TOKENIZER_CONTEXT_CAP - 2048 - TOOL_SCHEMA_TOKEN_RESERVE) * UNKNOWN_TOKENIZER_INPUT_PERCENT // 100
    )
    assert ModelTokenBudget.for_model(counter, 7000, 0, 2048).input_tokens == 684
    assert ModelTokenBudget.for_model(counter, 200000, 500, 2048).input_tokens == 500
    assert ModelTokenBudget.for_model(counter, 200000, 0, 10000).input_tokens == 0


@pytest.mark.parametrize("name", ["glm-5.3", "glm-5.3-flash"])
async def test_default_primary_budget_can_retain_more_than_old_provider_limit(name: str) -> None:
    counter = await load_token_counter(name)
    assert get_default_value("ai_chatbot_modern_context_tokens") == 0
    assert isinstance(counter.tokenizer, Tokenizer)
    # Distinguish the bundled GLM loader from the OpenAI estimate, not just its type.
    assert counter.count_text("Hello world!") == 3
    assert counter.count_text("Hello world!") != len(load_encoding("cl100k_base").encode_ordinary("Hello world!"))
    budget = ModelTokenBudget.for_model(counter, 200000, 0, 2048)
    history = ModernContext(redis=MagicMock(), chat_tid=1)
    history._budget = budget.input_tokens
    history._token_budgets = (budget, ModelTokenBudget(TokenCounter("unknown"), 1000))
    history.prompt = ["latest question"]
    messages = [
        ModelRequest(parts=[UserPromptPart("question")]),
        ModelResponse(parts=[TextPart("Hello world! " * 8000)]),
    ]
    assert counter.count_messages(messages) > 16384
    assert counter.count_messages(messages) < budget.input_tokens
    history._state.events = [history._event(messages, "turn")]
    history._prune()
    assert len(history._state.events) == 1
    assert ModelTokenBudget.for_model(counter, 200000, 10000, 2048).input_tokens == 10000


@pytest.mark.parametrize("name", ["glm-5.3", "glm-5.3-flash"])
async def test_candidate_projection_enforces_reserves_without_changing_primary(name: str) -> None:
    counter = await load_token_counter(name)
    primary = ModelTokenBudget.for_model(counter, 200000, 0, 2048)
    fallback_counter = await load_token_counter("unknown")
    fallback = ModelTokenBudget.for_model(fallback_counter, 32768, 0, 2048)
    history = ModernContext(redis=MagicMock(), chat_tid=1)
    history._budget = primary.input_tokens
    history._token_budgets = (primary, fallback)
    history.instructions = "Instructions"
    history._runtime_update = "<runtime_context>latest runtime</runtime_context>"
    history.prompt = ["current request"]
    history._state.events = [
        history._event(
            [
                ModelRequest(parts=[UserPromptPart(history._runtime_update)]),
                ModelResponse(parts=[TextPart("previous answer " * 6000)]),
            ],
            "turn",
        )
    ]
    history._prune()
    before = history._state.model_dump_json()
    model = TestModel(model_name="unknown")
    candidate = AIModelCandidate(model=model, model_name="unknown", context_window_tokens=32768)
    kwargs = _candidate_run_kwargs(
        {"user_prompt": history.prompt, "message_history": []},
        None,
        AIRequestOptions(context_projection=history.project_for_candidate),
        candidate,
        model,
    )
    assert kwargs["message_history"] == []
    assert history._runtime_update in kwargs["user_prompt"]
    assert history._state.model_dump_json() == before
    assert len(history._state.events) == 1
    history.prompt.append(BinaryContent(data=b"image", media_type="image/jpeg"))
    with pytest.raises(ValueError, match="exceeds its token budget"):
        history.project_for_candidate(candidate)
    assert history._state.model_dump_json() == before


async def test_output_tools_and_framing_reduce_available_input() -> None:
    counter = await load_token_counter("glm-5.3")
    budget = ModelTokenBudget.for_model(counter, 10000, 0, 2048)
    assert budget.input_tokens == 10000 - 2048 - 4096
    assert ModelTokenBudget.for_model(counter, 10000, 2000, 2048).input_tokens == 2000
    history = ModernContext(redis=MagicMock(), chat_tid=1)
    history._budget = budget.input_tokens
    history._token_budgets = (budget,)
    history.prompt = [BinaryContent(data=b"image", media_type="image/jpeg")]
    with pytest.raises(ValueError, match="exceeds its token budget"):
        history._prune()


@pytest.mark.parametrize("name", ["glm-5.3", "glm-5.3-flash"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_real_candidate_retry_projects_only_the_attempted_request(
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
    name: str,
) -> None:
    payloads: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload["model"] == name:
            assert "retained primary answer" in json.dumps(payload)
            return httpx2.Response(
                400, json={"error": {"message": "try another model", "type": "invalid_request_error"}}
            )
        assert "retained primary answer" not in json.dumps(payload)
        assert "latest question" in json.dumps(payload)
        if streaming:
            chunk = {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "unknown",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello"}, "finish_reason": None}],
            }
            finished = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(finished) + "\n\ndata: [DONE]\n\n",
            )
        return httpx2.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "unknown",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                ],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = OpenAIProvider(api_key="offline", http_client=client)
        primary = OpenAIChatModel(name, provider=provider)
        fallback = OpenAIChatModel("unknown", provider=provider)
        monkeypatch.setattr(ai_run, "get_ai_model", lambda name: fallback)
        plan = AIModelPlan(
            tuple(
                AIModelCandidate(model=model, model_name=model.model_name, context_window_tokens=200000)
                for model in (primary, fallback)
            )
        )
        # deepsource: skip - PYL-R1728 - async comprehension with await cannot be a generator for tuple()
        budgets = tuple(
            [
                ModelTokenBudget.for_model(await load_token_counter(model.model_name), 200000, 0, 2048)
                for model in (primary, fallback)
            ]
        )
        history = ModernContext(redis=MagicMock(), chat_tid=1)
        history._budget = budgets[0].input_tokens
        history._token_budgets = budgets
        history.prompt = ["latest question"]
        # Grow through multiple complete turns, beyond the observed 2-3 message loss.
        for turn in range(4):
            history._state.events.append(
                history._event(
                    [
                        ModelRequest(parts=[UserPromptPart(f"previous question {turn}")]),
                        ModelResponse(parts=[TextPart(f"retained primary answer {turn} " * 2000)]),
                    ],
                    "turn",
                )
            )
            history._prune()
            assert len(history._state.events) == turn + 1
        counter = budgets[0].counter
        assert isinstance(counter.tokenizer, Tokenizer)
        assert counter.count_text("Hello world!") == 3  # Bundled GLM fixture; cl100k differs.
        retained = [message for event in history._state.events for message in event.messages()]
        assert 16384 < counter.count_messages(retained) < budgets[0].input_tokens
        before = history._state.model_dump_json()
        options = AIRequestOptions(context_projection=history.project_for_candidate)
        agent = Agent(primary)
        if streaming:

            async def on_text(text: str) -> None:
                assert text == "hello"

            result = await ai_run.run_ai_stream(
                agent, history.prompt, on_text, request_options=options, model_plan=plan
            )
        else:
            result = await ai_run.run_ai_text(agent, history.prompt, request_options=options, model_plan=plan)
        assert result.output == "hello"
        assert result.served_model is fallback
        assert len(payloads) == 2
        assert history._state.model_dump_json() == before


async def test_framing_and_attachment_reserves_enforce_exact_boundary() -> None:
    counter = await load_token_counter("glm-5.3")
    history = ModernContext(redis=MagicMock(), chat_tid=1)
    history.instructions = "instructions"
    history.prompt = ["question", BinaryContent(data=b"image", media_type="image/jpeg")]
    messages = [ModelRequest(parts=[UserPromptPart("previous question")]), ModelResponse(parts=[TextPart("answer")])]
    framing = 64 + counter.count_text(history.instructions) + counter.count_content(history.prompt)
    assert framing > counter.IMAGE_TOKEN_RESERVE
    history._budget = framing + counter.count_messages(messages)
    history._token_budgets = (ModelTokenBudget(counter, history._budget),)
    history._state.events = [history._event(messages, "turn")]
    history._prune()
    assert len(history._state.events) == 1
    history._budget -= 1
    history._prune()
    assert history._state.events == []


async def test_same_upstream_name_uses_selected_candidates_registered_capacity() -> None:
    counter = await load_token_counter("glm-5.3-flash")
    primary = ModelTokenBudget.for_model(counter, 200000, 0, 2048)
    narrow = ModelTokenBudget.for_model(counter, 10000, 0, 2048)
    history = ModernContext(redis=MagicMock(), chat_tid=1)
    history._budget = primary.input_tokens
    history._token_budgets = (primary, narrow)
    history.prompt = ["current request"]
    history._state.events = [history._event([ModelResponse(parts=[TextPart("answer " * 10000)])], "turn")]
    history._prune()
    candidate = AIModelCandidate(
        model=TestModel(model_name=counter.model_name), model_name="other-provider/glm", context_window_tokens=10000
    )
    prompt, messages = history.project_for_candidate(candidate)
    assert prompt == history.prompt
    assert messages == []
    assert len(history._state.events) == 1
