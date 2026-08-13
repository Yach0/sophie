from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.settings import ModelSettings

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose, AIProviderKind
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_catalog import CatalogModel, ResolvedRole, catalog, resolve_roles
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan, build_model_plan
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


def pinned_candidate(model_name: str) -> AIModelCandidate:
    """A candidate for a model named by an ``ai_*_model`` flag rather than by a catalog role.

    The name may be a catalog model or an ad-hoc one the catalog has never heard of. The former
    keeps its declared capabilities; the latter is assumed capable, because that is what an operator
    pinning a model by hand gets today and narrowing it would silently skip their choice.
    """
    catalog_model = catalog().models.get(model_name)
    return AIModelCandidate(
        model=get_ai_model(model_name),
        model_name=model_name,
        supports_images=catalog_model.supports_images if catalog_model else True,
    )


def role_candidate(role: ResolvedRole) -> AIModelCandidate:
    """A candidate built for the role it came from, so its reasoning effort travels with it."""
    return AIModelCandidate(
        model=get_ai_model(role.model_name, reasoning_effort=role.reasoning_effort),
        model_name=role.model_name,
        supports_images=role.supports_images,
    )


def single_model_plan(model_name: str) -> AIModelPlan:
    """A plan for a purpose the catalog does not carry roles for, so it is pinned by flag alone.

    It has one candidate rather than none, so such a purpose still runs through the same failover
    machinery as every other and picks up the last-resort model when its one model fails.
    """
    return AIModelPlan(candidates=(pinned_candidate(model_name),))


async def build_purpose_plan(mode: AIMode, purpose: AIModelPurpose, override_name: str = "") -> AIModelPlan:
    """The ordered candidates serving a (mode, purpose), with a flag-pinned model in front.

    A pin still wins, but it now leads the list rather than replacing it: the pinned model runs
    exactly as before and the mode's own candidates stay behind it as the failover chain the pin
    never had. A pin is also a complete answer on its own, so only a purpose with neither a pin nor
    a catalog model is the operator mistake worth failing loudly on.
    """
    try:
        roles = await resolve_roles(mode, purpose)
    except ValueError:
        if not override_name:
            raise
        roles = ()

    return build_model_plan(
        [
            *((pinned_candidate(override_name),) if override_name else ()),
            *(role_candidate(role) for role in roles),
        ]
    )


async def get_proactive_replies_model_plan(chat_tid: int | None = None) -> AIModelPlan:
    return single_model_plan(str(await get_value("ai_proactive_replies_model", chat_tid=chat_tid)))


async def get_research_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_research_model", chat_tid=chat_tid))
    return get_ai_model(model_name)
