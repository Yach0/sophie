from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from aiogram.types import Message

from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.moderation.categories import MODERATION_CATEGORIES_TRANSLATES, ModerationCategory
from sophie_bot.modules.ai.utils.moderation.providers import (
    MistralModerationProvider,
    ModerationProvider,
    OpenAIModerationProvider,
)
from sophie_bot.modules.ai.utils.moderation.thresholds import (
    get_category_level,
    resolve_level_multipliers,
    resolve_thresholds,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import get_value
from sophie_bot.utils.logger import log

_PROVIDERS: Final[dict[str, ModerationProvider]] = {
    provider.name: provider for provider in (MistralModerationProvider(), OpenAIModerationProvider())
}
_DEFAULT_PROVIDER: Final[str] = MistralModerationProvider.name


@dataclass(frozen=True)
class ModerationResult:
    triggered: frozenset[ModerationCategory]
    triggered_native: frozenset[str]
    scores: dict[str, float]

    @property
    def flagged(self) -> bool:
        return bool(self.triggered)


async def get_moderation_provider(
    chat_tid: int | None = None,
    *,
    services: ApplicationServices,
) -> ModerationProvider:
    name = str(
        await get_value(
            "ai_moderation_provider",
            chat_tid=chat_tid,
            redis=services.redis,
        )
    )
    provider = _PROVIDERS.get(name)
    if provider is None:
        log.warning("Unknown AI moderation provider, falling back", provider=name, fallback=_DEFAULT_PROVIDER)
        return _PROVIDERS[_DEFAULT_PROVIDER]
    return provider


async def check_moderator(
    message: Message,
    settings: AIModeratorModel | None = None,
    chat_tid: int | None = None,
    *,
    services: ApplicationServices,
) -> ModerationResult:
    history = AIMessageHistory(services=services)
    await history.add_from_message(message, normalize_texts=True)

    provider = await get_moderation_provider(chat_tid, services=services)
    scores = await provider.classify(history, redis=services.redis)
    if not scores:
        return ModerationResult(triggered=frozenset(), triggered_native=frozenset(), scores={})

    resolved_thresholds = await resolve_thresholds(provider, chat_tid, redis=services.redis)
    multipliers = await resolve_level_multipliers(chat_tid, redis=services.redis)

    # The chat's detection level scales the score rather than the threshold, so one category can be
    # made more or less sensitive without disturbing the operator-tuned per-provider thresholds.
    adjusted: dict[str, float] = {}
    for native in provider.native_categories:
        level = get_category_level(settings, native.category.value)
        if level == DetectionLevel.OFF:
            continue
        adjusted[native.key] = scores.get(native.key, 0.0) * multipliers[level]

    triggered_native = frozenset(key for key, score in adjusted.items() if score >= resolved_thresholds[key])
    triggered = frozenset(native.category for native in provider.native_categories if native.key in triggered_native)

    log.debug(
        "AI moderation evaluated message",
        provider=provider.name,
        flagged=bool(triggered),
        triggered=sorted(triggered),
        triggered_native=sorted(triggered_native),
        scores=scores,
        adjusted_scores=adjusted,
        thresholds=resolved_thresholds,
        input_count=len(history.to_moderation),
    )

    return ModerationResult(triggered=triggered, triggered_native=triggered_native, scores=scores)


__all__ = (
    "MODERATION_CATEGORIES_TRANSLATES",
    "ModerationCategory",
    "ModerationResult",
    "check_moderator",
    "get_moderation_provider",
)
