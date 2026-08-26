from __future__ import annotations

from typing import Final

from sophie_bot.modules.ai.utils.ai_clients import get_openai_client
from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, run_ai_request_with_retries
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory, convert_to_openai_moderation_format
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory
from sophie_bot.modules.ai.utils.moderation.providers.base import NativeCategory

OPENAI_MODERATION_MODEL: Final[str] = "omni-moderation-latest"

# Keys are OpenAI's own category names; several of them fold onto one Sophie category.
# Sophie's health, financial, law and pii have no OpenAI equivalent and never fire on this backend.
# See https://platform.openai.com/docs/guides/moderation
_NATIVE_CATEGORIES: Final[tuple[NativeCategory, ...]] = (
    NativeCategory("sexual", "ai_moderation_threshold_openai_sexual", 0.5, ModerationCategory.SEXUAL),
    NativeCategory("sexual/minors", "ai_moderation_threshold_openai_sexual_minors", 0.2, ModerationCategory.SEXUAL),
    NativeCategory(
        "harassment",
        "ai_moderation_threshold_openai_harassment",
        0.5,
        ModerationCategory.HATE_AND_DISCRIMINATION,
    ),
    NativeCategory(
        "harassment/threatening",
        "ai_moderation_threshold_openai_harassment_threatening",
        0.4,
        ModerationCategory.HATE_AND_DISCRIMINATION,
    ),
    NativeCategory(
        "hate",
        "ai_moderation_threshold_openai_hate",
        0.4,
        ModerationCategory.HATE_AND_DISCRIMINATION,
    ),
    NativeCategory(
        "hate/threatening",
        "ai_moderation_threshold_openai_hate_threatening",
        0.3,
        ModerationCategory.HATE_AND_DISCRIMINATION,
    ),
    NativeCategory(
        "illicit",
        "ai_moderation_threshold_openai_illicit",
        0.4,
        ModerationCategory.DANGEROUS_AND_CRIMINAL_CONTENT,
    ),
    NativeCategory(
        "illicit/violent",
        "ai_moderation_threshold_openai_illicit_violent",
        0.3,
        ModerationCategory.DANGEROUS_AND_CRIMINAL_CONTENT,
    ),
    NativeCategory("self-harm", "ai_moderation_threshold_openai_self_harm", 0.3, ModerationCategory.SELFHARM),
    NativeCategory(
        "self-harm/intent",
        "ai_moderation_threshold_openai_self_harm_intent",
        0.3,
        ModerationCategory.SELFHARM,
    ),
    NativeCategory(
        "self-harm/instructions",
        "ai_moderation_threshold_openai_self_harm_instructions",
        0.3,
        ModerationCategory.SELFHARM,
    ),
    NativeCategory(
        "violence",
        "ai_moderation_threshold_openai_violence",
        0.4,
        ModerationCategory.VIOLENCE_AND_THREATS,
    ),
    NativeCategory(
        "violence/graphic",
        "ai_moderation_threshold_openai_violence_graphic",
        0.4,
        ModerationCategory.VIOLENCE_AND_THREATS,
    ),
)


class OpenAIModerationProvider:
    name: str = "openai"
    native_categories: tuple[NativeCategory, ...] = _NATIVE_CATEGORIES

    async def classify(self, history: AIMessageHistory) -> dict[str, float]:
        inputs = convert_to_openai_moderation_format(history.to_moderation)
        if not inputs:
            return {}

        client = await get_openai_client()
        response = await run_ai_request_with_retries(
            lambda: client.moderations.create(model=OPENAI_MODERATION_MODEL, input=inputs),
            AIErrorContext(operation="moderation", model_name=OPENAI_MODERATION_MODEL),
        )
        if not response.results:
            return {}

        # by_alias gives OpenAI's own names ("self-harm/intent"), matching NativeCategory.key.
        # Scores are None for categories OpenAI does not score in the caller's jurisdiction.
        scores = response.results[0].category_scores.model_dump(by_alias=True)
        return {key: float(value) for key, value in scores.items() if value is not None}
