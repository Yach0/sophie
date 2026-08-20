from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from httpx2 import HTTPError

from sophie_bot.db.models.ai.ai_catalog import (
    AICatalogModelModel,
    AICatalogProviderModel,
    AIModelPurpose,
    AIProviderKind,
)
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_catalog import (
    CATALOG_MODES,
    MODE_PURPOSES,
    AICatalog,
    bump_version,
    get_catalog,
    load_catalog,
    load_documents,
    mask_api_key,
    mode_allows,
)
from sophie_bot.modules.ai.utils.ai_chat_models import MODEL_OVERRIDE_FLAG_BY_PURPOSE
from sophie_bot.modules.ai.utils.ai_model_pricing import _openrouter_headers, _parse_price_per_million, ai_http_client
from sophie_bot.utils.api.auth import get_current_operator
from sophie_bot.utils.feature_flags import _SERVICE_TIER_VALUES

from .catalog_schemas import (
    CatalogExport,
    CatalogMeta,
    CatalogResolution,
    CatalogStatus,
    ImportResult,
    ModelCreate,
    ModelExport,
    ModelResponse,
    ModelUpdate,
    OpenRouterModelInfo,
    ProviderCreate,
    ProviderResponse,
    ProviderUpdate,
    ResolvedCandidate,
    ResolvedModel,
)

# Every route requires a valid operator JWT (issued by POST /auth/login/operator against the static
# operator token). The catalog holds provider API keys, so it must never be reachable unauthenticated.
router = APIRouter(
    prefix="/op/ai/catalog",
    tags=["ai_catalog"],
    dependencies=[Depends(get_current_operator)],
)


def _provider_response(provider: AICatalogProviderModel) -> ProviderResponse:
    return ProviderResponse(
        name=provider.name,
        kind=provider.kind,
        base_url=provider.base_url,
        enabled=provider.enabled,
        api_key_masked=mask_api_key(provider.api_key),
        has_key=bool(provider.api_key),
    )


def _model_response(model: AICatalogModelModel) -> ModelResponse:
    return ModelResponse(
        name=model.name,
        provider=model.provider,
        api_name=model.api_name,
        supports_reasoning=model.supports_reasoning,
        supports_images=model.supports_images,
        extra_params=model.extra_params,
        roles=model.roles,
        enabled=model.enabled,
    )


# ── Meta and status ────────────────────────────────────────────────────────


@router.get("/meta", response_model=CatalogMeta)
async def get_meta() -> CatalogMeta:
    return CatalogMeta(
        provider_kinds=[kind.value for kind in AIProviderKind],
        purposes=[purpose.value for purpose in AIModelPurpose],
        # Every mode that resolves a catalog model, including the two private-chat modes.
        modes=[mode.value for mode in CATALOG_MODES],
        mode_purposes={
            mode.value: [purpose.value for purpose in AIModelPurpose if purpose in purposes]
            for mode, purposes in MODE_PURPOSES.items()
        },
        model_override_flags={purpose.value: flag for purpose, flag in MODEL_OVERRIDE_FLAG_BY_PURPOSE.items()},
        service_tiers=sorted(_SERVICE_TIER_VALUES),
        reasoning_efforts=["low", "medium", "high"],
    )


@router.get("/status", response_model=CatalogStatus)
async def get_status() -> CatalogStatus:
    current = await get_catalog()
    return CatalogStatus(
        version=current.version,
        providers=len(current.providers),
        models=len(current.models),
        roles=len(current.roles),
    )


def _resolved_model(current: AICatalog, mode: AIMode, purpose: AIModelPurpose) -> ResolvedModel:
    candidates = current.roles_for(mode, purpose)
    return ResolvedModel(
        model=candidates[0].model_name if candidates else None,
        candidates=[
            ResolvedCandidate(
                model=role.model_name,
                priority=role.priority,
                supports_images=role.supports_images,
            )
            for role in candidates
        ],
    )


@router.get("/resolution", response_model=CatalogResolution)
async def get_resolution() -> CatalogResolution:
    current = await get_catalog()

    return CatalogResolution(
        modes=[mode.value for mode in CATALOG_MODES],
        purposes=[purpose.value for purpose in AIModelPurpose],
        # Only the purposes a mode can use — the same exact resolution Sophie runs, so the table
        # cannot drift from reality. A purpose a mode never uses is simply absent (the panel shows "—").
        per_mode={
            mode.value: {
                purpose.value: _resolved_model(current, mode, purpose)
                for purpose in AIModelPurpose
                if mode_allows(mode, purpose)
            }
            for mode in CATALOG_MODES
        },
    )


@router.post("/reload", response_model=CatalogStatus)
async def reload_catalog() -> CatalogStatus:
    """Force this process to rebuild its snapshot now, rather than on the next version check."""
    current = await load_catalog()
    return CatalogStatus(
        version=current.version,
        providers=len(current.providers),
        models=len(current.models),
        roles=len(current.roles),
    )


# ── Providers ──────────────────────────────────────────────────────────────


@router.get("/providers", response_model=list[ProviderResponse])
async def list_providers() -> list[ProviderResponse]:
    return [_provider_response(provider) for provider in await load_documents(AICatalogProviderModel)]


@router.post("/providers", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(data: ProviderCreate) -> ProviderResponse:
    if await AICatalogProviderModel.find_one(AICatalogProviderModel.name == data.name):
        raise HTTPException(status_code=409, detail="A provider with this name already exists")

    provider = AICatalogProviderModel(**data.model_dump())
    await provider.save()
    await bump_version()
    return _provider_response(provider)


@router.put("/providers/{name:path}", response_model=ProviderResponse)
async def update_provider(name: str, data: ProviderUpdate) -> ProviderResponse:
    provider = await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        # An omitted api_key keeps the stored one; an empty string is a deliberate clear.
        if field == "api_key" and value is None:
            continue
        setattr(provider, field, value)

    await provider.save()
    await bump_version()
    return _provider_response(provider)


@router.delete("/providers/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(name: str) -> None:
    provider = await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    await provider.delete()
    await bump_version()


# ── Models ─────────────────────────────────────────────────────────────────


@router.get("/models", response_model=list[ModelResponse])
async def list_models() -> list[ModelResponse]:
    return [_model_response(model) for model in await load_documents(AICatalogModelModel)]


@router.post("/models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(data: ModelCreate) -> ModelResponse:
    if await AICatalogModelModel.find_one(AICatalogModelModel.name == data.name):
        raise HTTPException(status_code=409, detail="A model with this name already exists")

    model = AICatalogModelModel(**data.model_dump())
    await model.save()
    await bump_version()
    return _model_response(model)


@router.put("/models/{name:path}", response_model=ModelResponse)
async def update_model(name: str, data: ModelUpdate) -> ModelResponse:
    model = await AICatalogModelModel.find_one(AICatalogModelModel.name == name)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(model, field, value)

    await model.save()
    await bump_version()
    return _model_response(model)


@router.delete("/models/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(name: str) -> None:
    model = await AICatalogModelModel.find_one(AICatalogModelModel.name == name)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    await model.delete()
    await bump_version()


# ── Import / export ────────────────────────────────────────────────────────


@router.get("/export", response_model=CatalogExport)
async def export_catalog() -> CatalogExport:
    """The catalog's models as one JSON document, to import into another instance.

    Providers are not exported: their keys and endpoints are per-instance, so each keeps its own.
    """
    models = await load_documents(AICatalogModelModel)
    return CatalogExport(
        models=[
            ModelExport(
                name=model.name,
                provider=model.provider,
                api_name=model.api_name,
                supports_reasoning=model.supports_reasoning,
                supports_images=model.supports_images,
                extra_params=model.extra_params,
                roles=model.roles,
                enabled=model.enabled,
            )
            for model in models
        ],
    )


@router.post("/import", response_model=ImportResult)
async def import_catalog(data: CatalogExport, replace: bool = False) -> ImportResult:
    """Apply an exported set of models.

    Default is a merge: models are upserted by name and anything not in the file is left alone.
    ``replace=true`` deletes any model not present in the file first, making the target an exact copy.
    Providers are never touched — a model referencing a provider the target lacks simply will not
    resolve until that provider is configured here.
    """
    result = ImportResult(models_created=0, models_updated=0, deleted=0)

    if replace:
        keep = {model.name for model in data.models}
        for existing in await load_documents(AICatalogModelModel):
            if existing.name not in keep:
                await existing.delete()
                result.deleted += 1

    for model in data.models:
        stored = await AICatalogModelModel.find_one(AICatalogModelModel.name == model.name)
        if stored:
            for field, value in model.model_dump().items():
                setattr(stored, field, value)
            await stored.save()
            result.models_updated += 1
        else:
            await AICatalogModelModel(**model.model_dump()).save()
            result.models_created += 1

    await bump_version()
    return result


# ── Model picker ───────────────────────────────────────────────────────────

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _parse_models(items: list[dict]) -> list[OpenRouterModelInfo]:
    """Read the OpenAI-compatible ``/models`` shape. Only OpenRouter enriches it with pricing etc.;
    a plain OpenAI-compatible endpoint returns little more than an id, which is all the picker needs."""
    models = []
    for item in items:
        model_id = item.get("id")
        if not model_id:
            continue
        pricing = item.get("pricing") or {}
        architecture = item.get("architecture") or {}
        models.append(
            OpenRouterModelInfo(
                id=model_id,
                name=item.get("name") or model_id,
                description=item.get("description"),
                context_length=item.get("context_length"),
                prompt_price=_parse_price_per_million(pricing.get("prompt")),
                completion_price=_parse_price_per_million(pricing.get("completion")),
                modalities=architecture.get("input_modalities") or [],
            )
        )
    return models


async def _fetch_models(url: str, headers: dict[str, str]) -> list[OpenRouterModelInfo]:
    try:
        response = await ai_http_client.get(url, headers=headers)
        response.raise_for_status()
    except HTTPError as err:
        raise HTTPException(status_code=502, detail=f"Could not reach {url}: {err}") from err
    return _parse_models(response.json().get("data", []))


@router.get("/openrouter/models", response_model=list[OpenRouterModelInfo])
async def list_openrouter_models() -> list[OpenRouterModelInfo]:
    """Proxy OpenRouter's model list so the panel can pick a model without an OpenRouter key of its own."""
    return await _fetch_models(_OPENROUTER_MODELS_URL, _openrouter_headers())


@router.get("/providers/{name:path}/models", response_model=list[OpenRouterModelInfo])
async def list_provider_models(name: str) -> list[OpenRouterModelInfo]:
    """List the models a configured provider offers, from its OpenAI-compatible ``/models`` endpoint.

    OpenRouter providers go to OpenRouter; an OpenAI-compatible provider is queried at its own
    ``base_url`` with its stored key, so the picker can offer that provider's models too.
    """
    provider = await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if provider.kind is AIProviderKind.openrouter:
        headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else _openrouter_headers()
        return await _fetch_models(_OPENROUTER_MODELS_URL, headers)

    if not provider.base_url:
        raise HTTPException(status_code=400, detail="This provider has no base_url to query")
    url = f"{provider.base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    return await _fetch_models(url, headers)
