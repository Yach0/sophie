"""AI-related database models."""

from sophie_bot.db.models.ai.ai_autotranslate import AIAutotranslateModel
from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryLine, AIChatSummaryModel
from sophie_bot.db.models.ai.ai_enabled import AIEnabledModel
from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.db.models.ai.ai_provider import AIProviderModel
from sophie_bot.db.models.ai.ai_quota import AIQuotaModel
from sophie_bot.db.models.ai.ai_usage import AIUsageModel
from sophie_bot.utils.ai_features import (
    AI_FEATURE_AUTO_TRANSLATE,
    AI_FEATURE_CHATBOT,
    AI_FEATURE_FILTER,
    AI_FEATURE_TRANSLATE,
)

__all__ = [
    "AIAutotranslateModel",
    "AIChatSummaryLine",
    "AIChatSummaryModel",
    "AIEnabledModel",
    "AIMemoryModel",
    "AIModeratorModel",
    "AIProviderModel",
    "AIQuotaModel",
    "AIUsageModel",
    "DetectionLevel",
    "AI_FEATURE_CHATBOT",
    "AI_FEATURE_TRANSLATE",
    "AI_FEATURE_AUTO_TRANSLATE",
    "AI_FEATURE_FILTER",
]
