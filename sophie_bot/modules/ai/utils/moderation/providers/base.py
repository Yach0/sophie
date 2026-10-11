from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from openai import APIConnectionError
from redis.asyncio import Redis

from sophie_bot.modules.ai.utils.ai_errors import (
    AI_PROVIDER_EXCEPTIONS,
    AIErrorContext,
    capture_ai_error,
    is_retryable_ai_provider_error,
    run_ai_request_with_retries,
)
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory
from sophie_bot.utils.feature_flags import FeatureType
from sophie_bot.utils.logger import log


class ModerationUnavailable(Exception):
    """A transient classifier failure after the existing retry budget was exhausted."""


async def run_moderation_request[ModerationOutputT](
    operation: Callable[[], Awaitable[ModerationOutputT]],
    context: AIErrorContext,
) -> ModerationOutputT:
    try:
        return await run_ai_request_with_retries(operation, context)
    except AI_PROVIDER_EXCEPTIONS as error:
        # OpenAI wraps transport errors after SDK retries; do not add another retry budget.
        if not is_retryable_ai_provider_error(error) and not isinstance(error, APIConnectionError):
            raise
        event_id = capture_ai_error(error, context)
        log.warning(
            "AI moderation unavailable after provider retries",
            operation=context.operation,
            model=context.model_name,
            error_type=type(error).__name__,
            sentry_event_id=event_id,
        )
        # The handled signal must not retain private SDK input in its traceback.
        raise ModerationUnavailable from None


@dataclass(frozen=True)
class NativeCategory:
    """One category as the provider itself reports it.

    Thresholds live at this level rather than on the normalised category, because grouped
    categories score on different distributions: `sexual/minors` needs a far lower cut-off than
    `sexual`, yet both surface to users as ``ModerationCategory.SEXUAL``.
    """

    key: str
    flag: FeatureType
    default_threshold: float
    category: ModerationCategory


class ModerationProvider(Protocol):
    name: str
    native_categories: tuple[NativeCategory, ...]

    async def classify(
        self,
        history: AIMessageHistory,
        *,
        redis: Redis,
    ) -> dict[str, float]:
        """Return the provider's raw per-native-category scores."""
        ...
