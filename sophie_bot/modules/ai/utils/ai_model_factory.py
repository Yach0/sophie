from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.settings import ModelSettings

from sophie_bot.db.models.ai.ai_catalog import AIProviderKind
from sophie_bot.modules.ai.utils.ai_catalog import CatalogModel, catalog
from sophie_bot.modules.ai.utils.ai_providers import get_openai_provider, get_openrouter_provider
from sophie_bot.utils.feature_flags import get_value

_ai_models: dict[str, Model] = {}
_cache_version = ""

# Default reasoning effort applied to every model to keep token costs down. Models that do not
# support reasoning simply ignore the parameter on OpenRouter; per-model extra_params win on conflict.
_DEFAULT_REASONING_EFFORT = "low"


def _build_openrouter_settings(
    model: CatalogModel | None, reasoning_effort: str | None
) -> OpenRouterModelSettings | None:
    extra_params: dict[str, object] = {}
    if model is None or model.supports_reasoning:
        extra_params["openrouter_reasoning"] = {"effort": reasoning_effort or _DEFAULT_REASONING_EFFORT}
    if model and model.extra_params:
        extra_params.update(model.extra_params)
    return OpenRouterModelSettings(**extra_params) if extra_params else None


def _build_openai_settings(model: CatalogModel) -> ModelSettings | None:
    # OpenRouter-only keys such as openrouter_reasoning mean nothing to a plain OpenAI-compatible
    # endpoint, so only explicit per-model extra_params are forwarded here.
    return ModelSettings(**model.extra_params) if model.extra_params else None


def _build_model(model_name: str, reasoning_effort: str | None) -> Model:
    """Build a model from the catalog.

    A name absent from the catalog — an ad-hoc value set through an ``ai_*_model`` flag — goes to
    the OpenRouter provider as-is.
    """
    model = catalog().models.get(model_name)

    if model and model.provider.kind is AIProviderKind.openai_compatible:
        return OpenAIChatModel(
            model.api_name,
            provider=get_openai_provider(model.provider),
            settings=_build_openai_settings(model),
        )

    provider = model.provider if model else None
    return OpenRouterModel(
        model.api_name if model else model_name,
        provider=get_openrouter_provider(provider),
        settings=_build_openrouter_settings(model, reasoning_effort),
    )


def get_ai_model(model_name: str, reasoning_effort: str | None = None) -> Model:
    global _cache_version

    # A built model holds a provider client and its settings, both of which a catalog reload may
    # have changed, so the cache is dropped whenever the snapshot it was built from is replaced.
    version = catalog().version
    if version != _cache_version:
        _ai_models.clear()
        _cache_version = version

    # The same model can serve several roles at different reasoning efforts, so the effort is part
    # of the cache key.
    key = f"{model_name}\x00{reasoning_effort or ''}"
    if key not in _ai_models:
        _ai_models[key] = _build_model(model_name, reasoning_effort)
    return _ai_models[key]


async def get_proactive_replies_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_proactive_replies_model", chat_tid=chat_tid))
    return get_ai_model(model_name)


async def get_research_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_research_model", chat_tid=chat_tid))
    return get_ai_model(model_name)
