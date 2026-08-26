from __future__ import annotations

from typing import Final, cast

from mistralai.client.models.chatmoderationrequest import ChatModerationRequestInputs3
from mistralai.client.models.moderationobject import ModerationObject
from mistralai.client.models.moderationresponse import ModerationResponse

from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client
from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, run_ai_request_with_retries
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory, convert_to_moderation_format
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory
from sophie_bot.modules.ai.utils.moderation.providers.base import NativeCategory

MISTRAL_MODERATION_MODEL: Final[str] = "mistral-moderation-latest"

# Mistral's taxonomy is already Sophie's: every native category folds onto itself.
# See https://docs.mistral.ai/capabilities/guardrailing/
_NATIVE_CATEGORIES: Final[tuple[NativeCategory, ...]] = (
    NativeCategory("sexual", "ai_moderation_threshold_mistral_sexual", 0.5, ModerationCategory.SEXUAL),
    NativeCategory(
        "hate_and_discrimination",
        "ai_moderation_threshold_mistral_hate_and_discrimination",
        0.4,
        ModerationCategory.HATE_AND_DISCRIMINATION,
    ),
    NativeCategory(
        "violence_and_threats",
        "ai_moderation_threshold_mistral_violence_and_threats",
        0.4,
        ModerationCategory.VIOLENCE_AND_THREATS,
    ),
    NativeCategory(
        "dangerous_and_criminal_content",
        "ai_moderation_threshold_mistral_dangerous_and_criminal_content",
        0.4,
        ModerationCategory.DANGEROUS_AND_CRIMINAL_CONTENT,
    ),
    NativeCategory("selfharm", "ai_moderation_threshold_mistral_selfharm", 0.3, ModerationCategory.SELFHARM),
    NativeCategory("health", "ai_moderation_threshold_mistral_health", 0.3, ModerationCategory.HEALTH),
    NativeCategory("financial", "ai_moderation_threshold_mistral_financial", 0.3, ModerationCategory.FINANCIAL),
    NativeCategory("law", "ai_moderation_threshold_mistral_law", 0.3, ModerationCategory.LAW),
    NativeCategory("pii", "ai_moderation_threshold_mistral_pii", 0.3, ModerationCategory.PII),
)


class MistralModerationProvider:
    name: str = "mistral"
    native_categories: tuple[NativeCategory, ...] = _NATIVE_CATEGORIES

    async def classify(self, history: AIMessageHistory) -> dict[str, float]:
        moderation_messages = cast(ChatModerationRequestInputs3, convert_to_moderation_format(history.to_moderation))
        client = await get_mistral_client()
        response: ModerationResponse = await run_ai_request_with_retries(
            lambda: client.classifiers.moderate_chat_async(
                inputs=moderation_messages,
                model=MISTRAL_MODERATION_MODEL,
            ),
            AIErrorContext(operation="moderation", model_name=MISTRAL_MODERATION_MODEL),
        )
        if not response.results:
            return {}

        result: ModerationObject = response.results[0]
        return dict(result.category_scores or {})
