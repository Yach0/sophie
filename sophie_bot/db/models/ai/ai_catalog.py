from __future__ import annotations

from enum import Enum
from typing import ClassVar

import pymongo
from beanie import Document
from pydantic import BaseModel, Field

from sophie_bot.db.models.ai.ai_mode import AIMode


class AIProviderKind(str, Enum):
    """How requests reach a provider. Decides both the client and which settings it understands."""

    openrouter = "openrouter"
    openai_compatible = "openai_compatible"
    # Reached with the vendor's own SDK rather than a chat-completions client: these rows carry the
    # key for a first-party service (moderation classifiers, audio transcription), not a model.
    moderation = "moderation"


class AIModelPurpose(str, Enum):
    chatbot = "chatbot"
    translation = "translation"
    filters = "filters"
    summary = "summary"
    moderation_reason = "moderation_reason"
    sophie_inspect = "sophie_inspect"
    research = "research"


class AIModelRole(BaseModel):
    """What a model is used for: a specific chat mode paired with a purpose.

    Resolution is exact — there is no any-mode wildcard — so ``mode`` is always a concrete mode.

    ``service_tier`` and ``reasoning_effort`` are per role, so one model can be flex-tier for
    research and normal for chatbot at once. When unset, the feature-flag service tier and the
    default reasoning effort apply, exactly as before.

    Several models may claim the same (mode, purpose): they form that combination's ordered
    candidate list, tried best-first. ``priority`` orders them — lower runs earlier — and ties break
    on the model name so the order never depends on how the rows happen to come back from Mongo.
    """

    mode: AIMode
    purpose: AIModelPurpose
    service_tier: str | None = None
    reasoning_effort: str | None = None
    # Lower wins. Rows written before candidate lists existed all land on 0, which keeps a
    # single-model purpose behaving exactly as it did.
    priority: int = 0


class AICatalogProviderModel(Document):
    name: str
    kind: AIProviderKind = AIProviderKind.openai_compatible
    base_url: str | None = None
    api_key: str = ""
    enabled: bool = True

    class Settings:
        name = "ai_catalog_provider"
        indexes: ClassVar = [pymongo.IndexModel("name", unique=True)]


class AICatalogModelModel(Document):
    name: str
    provider: str
    # Name sent upstream when it differs from ``name``, which carries the "<provider>/" prefix for
    # OpenAI-compatible providers so model names stay unique across the catalog.
    api_name: str | None = None
    supports_reasoning: bool = True
    # Whether this model can be shown an image. A request carrying one skips the candidates that
    # cannot. Defaults to true so rows written before this field keep serving images as they did —
    # an operator marks a cheap text-only model false to route image turns past it.
    supports_images: bool = True
    extra_params: dict[str, object] | None = None
    roles: list[AIModelRole] = Field(default_factory=list)
    enabled: bool = True

    class Settings:
        name = "ai_catalog_model"
        indexes: ClassVar = [pymongo.IndexModel("name", unique=True)]
