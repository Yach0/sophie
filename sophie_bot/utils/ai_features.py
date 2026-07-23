from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AIFeature = Literal["chatbot", "translate", "auto_translate", "filter", "research", "deep_help"]

AI_FEATURE_CHATBOT: AIFeature = "chatbot"
AI_FEATURE_TRANSLATE: AIFeature = "translate"
AI_FEATURE_AUTO_TRANSLATE: AIFeature = "auto_translate"
AI_FEATURE_FILTER: AIFeature = "filter"
AI_FEATURE_RESEARCH: AIFeature = "research"
AI_FEATURE_DEEP_HELP: AIFeature = "deep_help"


@dataclass(frozen=True)
class AIFeatureInfo:
    key: AIFeature
    title: str
    icon: str


AI_FEATURES: tuple[AIFeatureInfo, ...] = (
    AIFeatureInfo(AI_FEATURE_CHATBOT, "Chatbot", "\U0001f916"),
    AIFeatureInfo(AI_FEATURE_TRANSLATE, "Translate", "\U0001f310"),
    AIFeatureInfo(AI_FEATURE_AUTO_TRANSLATE, "Auto-translate", "\U0001f5e3\ufe0f"),
    AIFeatureInfo(AI_FEATURE_FILTER, "AI Filter", "\U0001f9ee"),
    AIFeatureInfo(AI_FEATURE_RESEARCH, "Research", "\U0001f50e"),
    AIFeatureInfo(AI_FEATURE_DEEP_HELP, "Deep help", "\U0001f9ed"),
)

AI_FEATURES_BY_KEY: dict[AIFeature, AIFeatureInfo] = {feature.key: feature for feature in AI_FEATURES}
