from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from httpx import HTTPError

from sophie_bot.db.models.ai.ai_catalog import (
    AICatalogModelModel,
    AICatalogProviderModel,
    AIModelPurpose,
    AIProviderKind,
)
from sophie_bot.db.models.ai.ai_mode import SELECTABLE_MODES
from sophie_bot.modules.ai.utils.ai_catalog import bump_version, get_catalog, load_catalog, mask_api_key
from sophie_bot.modules.ai.utils.ai_model_pricing import ai_http_client, _openrouter_headers, _parse_price_per_million
from sophie_bot.utils.api.auth import get_current_operator

from .catalog_schemas import (
    CatalogMeta,
    CatalogStatus,
    ModelCreate,
    ModelResponse,
    ModelUpdate,
    OpenRouterModelInfo,
    ProviderCreate,
    ProviderResponse,
    ProviderUpdate,
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
        # Only the modes a chat can actually be in: the two private-chat-only ones are resolved per
        # message and never carry a catalog role.
        modes=[mode.value for mode in SELECTABLE_MODES],
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
    return [_provider_response(provider) async for provider in AICatalogProviderModel.find_all()]


@router.post("/providers", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(data: ProviderCreate) -> ProviderResponse:
    if await AICatalogProviderModel.find_one(AICatalogProviderModel.name == data.name):
        raise HTTPException(status_code=409, detail="A provider with this name already exists")

    provider = AICatalogProviderModel(**data.model_dump())
    await provider.save()
    await bump_version()
    return _provider_response(provider)


@router.put("/providers/{name}", response_model=ProviderResponse)
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


@router.delete("/providers/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(name: str) -> None:
    provider = await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    await provider.delete()
    await bump_version()


# ── Models ─────────────────────────────────────────────────────────────────


@router.get("/models", response_model=list[ModelResponse])
async def list_models() -> list[ModelResponse]:
    return [_model_response(model) async for model in AICatalogModelModel.find_all()]


@router.post("/models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(data: ModelCreate) -> ModelResponse:
    if await AICatalogModelModel.find_one(AICatalogModelModel.name == data.name):
        raise HTTPException(status_code=409, detail="A model with this name already exists")

    model = AICatalogModelModel(**data.model_dump())
    await model.save()
    await bump_version()
    return _model_response(model)


@router.put("/models/{name}", response_model=ModelResponse)
async def update_model(name: str, data: ModelUpdate) -> ModelResponse:
    model = await AICatalogModelModel.find_one(AICatalogModelModel.name == name)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(model, field, value)

    await model.save()
    await bump_version()
    return _model_response(model)


@router.delete("/models/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(name: str) -> None:
    model = await AICatalogModelModel.find_one(AICatalogModelModel.name == name)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    await model.delete()
    await bump_version()


# ── OpenRouter model picker ────────────────────────────────────────────────


@router.get("/openrouter/models", response_model=list[OpenRouterModelInfo])
async def list_openrouter_models() -> list[OpenRouterModelInfo]:
    """Proxy OpenRouter's model list so the panel can pick a model without an OpenRouter key of its own."""
    try:
        response = await ai_http_client.get("https://openrouter.ai/api/v1/models", headers=_openrouter_headers())
        response.raise_for_status()
    except HTTPError as err:
        raise HTTPException(status_code=502, detail=f"Could not reach OpenRouter: {err}") from err

    models = []
    for item in response.json().get("data", []):
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
