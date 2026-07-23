from __future__ import annotations

from enum import Enum

import pymongo
from beanie import Document
from pydantic import BaseModel, Field

from sophie_bot.db.models.ai.ai_mode import AIMode


class AIProviderKind(str, Enum):
    """How requests reach a provider. Decides both the client and which settings it understands."""

    openrouter = "openrouter"
    openai_compatible = "openai_compatible"


class AIModelPurpose(str, Enum):
    chatbot = "chatbot"
    translation = "translation"
    filters = "filters"
    summary = "summary"
    moderation_reason = "moderation_reason"
    sophie_inspect = "sophie_inspect"


class AIModelRole(BaseModel):
    """What a model is used for. ``mode`` is None for purposes that are not per-chat."""

    mode: AIMode | None = None
    purpose: AIModelPurpose


class AICatalogProviderModel(Document):
    name: str
    kind: AIProviderKind = AIProviderKind.openai_compatible
    base_url: str | None = None
    api_key: str = ""
    enabled: bool = True

    class Settings:
        name = "ai_catalog_provider"
        indexes = [pymongo.IndexModel("name", unique=True)]


class AICatalogModelModel(Document):
    name: str
    provider: str
    # Name sent upstream when it differs from ``name``, which carries the "<provider>/" prefix for
    # OpenAI-compatible providers so model names stay unique across the catalog.
    api_name: str | None = None
    supports_reasoning: bool = True
    extra_params: dict[str, object] | None = None
    roles: list[AIModelRole] = Field(default_factory=list)
    enabled: bool = True

    class Settings:
        name = "ai_catalog_model"
        indexes = [pymongo.IndexModel("name", unique=True)]
