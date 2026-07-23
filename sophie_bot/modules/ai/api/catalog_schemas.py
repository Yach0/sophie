from __future__ import annotations

from pydantic import BaseModel, Field

from sophie_bot.db.models.ai.ai_catalog import AIModelRole, AIProviderKind


class ProviderResponse(BaseModel):
    name: str
    kind: AIProviderKind
    base_url: str | None
    enabled: bool
    api_key_masked: str
    has_key: bool


class ProviderCreate(BaseModel):
    name: str
    kind: AIProviderKind = AIProviderKind.openai_compatible
    base_url: str | None = None
    api_key: str = ""
    enabled: bool = True


class ProviderUpdate(BaseModel):
    kind: AIProviderKind | None = None
    base_url: str | None = None
    # None leaves the stored key untouched, so the panel never has to round-trip a secret it was
    # never shown.
    api_key: str | None = None
    enabled: bool | None = None


class ModelResponse(BaseModel):
    name: str
    provider: str
    api_name: str | None
    supports_reasoning: bool
    extra_params: dict[str, object] | None
    roles: list[AIModelRole]
    enabled: bool


class ModelCreate(BaseModel):
    name: str
    provider: str
    api_name: str | None = None
    supports_reasoning: bool = True
    extra_params: dict[str, object] | None = None
    roles: list[AIModelRole] = Field(default_factory=list)
    enabled: bool = True


class ModelUpdate(BaseModel):
    provider: str | None = None
    api_name: str | None = None
    supports_reasoning: bool | None = None
    extra_params: dict[str, object] | None = None
    roles: list[AIModelRole] | None = None
    enabled: bool | None = None


class OpenRouterModelInfo(BaseModel):
    id: str
    name: str
    description: str | None = None
    context_length: int | None = None
    prompt_price: float | None = None
    completion_price: float | None = None
    modalities: list[str] = Field(default_factory=list)


class CatalogMeta(BaseModel):
    """The enum values the panel needs to build role and provider pickers without hardcoding them."""

    provider_kinds: list[str]
    purposes: list[str]
    modes: list[str]


class CatalogStatus(BaseModel):
    version: str
    providers: int
    models: int
    roles: int


class ResolvedModel(BaseModel):
    model: str | None
    # True when the mode has no model of its own for this purpose and the support tier answered.
    fallback: bool


class ModelExport(BaseModel):
    name: str
    provider: str
    api_name: str | None = None
    supports_reasoning: bool = True
    extra_params: dict[str, object] | None = None
    roles: list[AIModelRole] = Field(default_factory=list)
    enabled: bool = True


class CatalogExport(BaseModel):
    """The catalog's models as one document, for moving them between instances.

    Providers are deliberately left out: their API keys and endpoints are environment-specific, so
    each instance keeps its own and only the model definitions travel.
    """

    schema_version: int = 1
    models: list[ModelExport] = Field(default_factory=list)


class ImportResult(BaseModel):
    models_created: int
    models_updated: int
    deleted: int


class CatalogResolution(BaseModel):
    """What Sophie actually uses right now, mode by mode — the model each (mode, purpose) resolves to.

    Mirrors the resolution in ai_chat_models: chatbot/translation/filters are per chat mode, while
    summary/moderation_reason/sophie_inspect are the same for every chat.
    """

    modes: list[str]
    per_chat_purposes: list[str]
    global_purposes: list[str]
    per_mode: dict[str, dict[str, ResolvedModel]]
    globals: dict[str, ResolvedModel]
