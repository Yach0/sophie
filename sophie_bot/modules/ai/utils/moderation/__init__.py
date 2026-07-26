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


async def get_moderation_provider(chat_tid: int | None = None) -> ModerationProvider:
    name = str(await get_value("ai_moderation_provider", chat_tid=chat_tid))
    provider = _PROVIDERS.get(name)
    if provider is None:
        log.warning("Unknown AI moderation provider, falling back", provider=name, fallback=_DEFAULT_PROVIDER)
        return _PROVIDERS[_DEFAULT_PROVIDER]
    return provider


async def check_moderator(
    message: Message,
    settings: AIModeratorModel | None = None,
    chat_tid: int | None = None,
) -> ModerationResult:
    history = AIMessageHistory()
    await history.add_from_message(message, normalize_texts=True)

    provider = await get_moderation_provider(chat_tid)
    scores = await provider.classify(history)
    if not scores:
        return ModerationResult(triggered=frozenset(), triggered_native=frozenset(), scores={})

    thresholds = await resolve_thresholds(provider, chat_tid)
    multipliers = await resolve_level_multipliers(chat_tid)

    # The chat's detection level scales the score rather than the threshold, so one category can be
    # made more or less sensitive without disturbing the operator-tuned per-provider thresholds.
    adjusted: dict[str, float] = {}
    for native in provider.native_categories:
        level = get_category_level(settings, native.category.value)
        if level == DetectionLevel.OFF:
            continue
        adjusted[native.key] = scores.get(native.key, 0.0) * multipliers[level]

    triggered_native = frozenset(key for key, score in adjusted.items() if score >= thresholds[key])
    triggered = frozenset(native.category for native in provider.native_categories if native.key in triggered_native)

    log.debug(
        "AI moderation evaluated message",
        provider=provider.name,
        flagged=bool(triggered),
        triggered=sorted(triggered),
        triggered_native=sorted(triggered_native),
        scores=scores,
        adjusted_scores=adjusted,
        thresholds=thresholds,
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
