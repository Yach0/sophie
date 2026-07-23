from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from sophie_bot.db.models.ai.ai_catalog import (
    AICatalogModelModel,
    AICatalogProviderModel,
    AIModelPurpose,
    AIModelRole,
)
from sophie_bot.modules.ai.api import catalog
from sophie_bot.modules.ai.api.catalog_schemas import (
    ModelCreate,
    ModelUpdate,
    ProviderCreate,
    ProviderUpdate,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("db_init")]


@pytest.fixture(autouse=True)
def _no_version_bump():
    # bump_version writes to Redis, which the DB fixture does not provide; the routes are covered
    # for it by asserting the mock is awaited where it matters.
    with patch.object(catalog, "bump_version", AsyncMock()) as bump:
        yield bump


async def _clear() -> None:
    await AICatalogProviderModel.delete_all()
    await AICatalogModelModel.delete_all()


async def test_create_provider_refuses_a_duplicate_name(_no_version_bump) -> None:
    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter", api_key="sk-1234abcd"))

    with pytest.raises(HTTPException) as error:
        await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter"))

    assert error.value.status_code == 409


async def test_provider_list_masks_the_key_and_never_returns_it(_no_version_bump) -> None:
    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter", api_key="sk-abcdef1234"))

    result = await catalog.list_providers()

    assert result[0].has_key
    assert result[0].api_key_masked == "sk-…1234"
    # The plaintext key is not a field of the response model at all.
    assert "api_key" not in result[0].model_dump()


async def test_updating_a_provider_without_a_key_keeps_the_stored_one(_no_version_bump) -> None:
    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter", api_key="sk-original"))

    await catalog.update_provider("openrouter", ProviderUpdate(enabled=False))

    stored = await AICatalogProviderModel.find_one(AICatalogProviderModel.name == "openrouter")
    assert stored.api_key == "sk-original"
    assert stored.enabled is False
    # Every mutation must invalidate the running catalog, or the change never reaches Sophie.
    assert _no_version_bump.await_count >= 1


async def test_updating_a_provider_with_an_empty_key_clears_it(_no_version_bump) -> None:
    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter", api_key="sk-original"))

    await catalog.update_provider("openrouter", ProviderUpdate(api_key=""))

    stored = await AICatalogProviderModel.find_one(AICatalogProviderModel.name == "openrouter")
    assert stored.api_key == ""


async def test_creating_a_model_carries_its_roles(_no_version_bump) -> None:
    await _clear()
    role = AIModelRole(mode=None, purpose=AIModelPurpose.summary)

    result = await catalog.create_model(
        ModelCreate(name="openai/gpt-5.5", provider="openrouter", roles=[role])
    )

    assert result.roles == [role]
    stored = await AICatalogModelModel.find_one(AICatalogModelModel.name == "openai/gpt-5.5")
    assert stored.provider == "openrouter"


async def test_deleting_a_model_removes_it(_no_version_bump) -> None:
    await _clear()
    await catalog.create_model(ModelCreate(name="openai/gpt-5.5", provider="openrouter"))

    await catalog.delete_model("openai/gpt-5.5")

    assert await AICatalogModelModel.find_one(AICatalogModelModel.name == "openai/gpt-5.5") is None


async def test_updating_a_missing_model_is_a_404(_no_version_bump) -> None:
    await _clear()
    with pytest.raises(HTTPException) as error:
        await catalog.update_model("nope", ModelUpdate(enabled=False))

    assert error.value.status_code == 404


async def test_meta_exposes_the_enums_the_panel_builds_pickers_from() -> None:
    meta = await catalog.get_meta()

    assert "openrouter" in meta.provider_kinds
    assert "sophie_inspect" in meta.purposes
    # The two private-chat-only modes never carry a role, so they must not be offered.
    assert "sophie_pm" not in meta.modes
    assert "support" in meta.modes


async def test_openrouter_proxy_trims_the_upstream_shape() -> None:
    payload = {
        "data": [
            {
                "id": "openai/gpt-5.5",
                "name": "GPT-5.5",
                "description": "d",
                "context_length": 400000,
                "pricing": {"prompt": "0.0000015", "completion": "0.000006"},
                "architecture": {"input_modalities": ["text", "image"]},
            },
            # An entry with no id cannot be stored by name, so it is dropped.
            {"name": "no id"},
        ]
    }
    response = SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)
    with patch.object(catalog.ai_http_client, "get", AsyncMock(return_value=response)):
        models = await catalog.list_openrouter_models()

    assert len(models) == 1
    model = models[0]
    assert model.id == "openai/gpt-5.5"
    assert model.prompt_price == 1.5
    assert model.completion_price == 6.0
    assert model.modalities == ["text", "image"]


async def test_openrouter_proxy_reports_upstream_failure_as_502() -> None:
    from httpx import HTTPError

    with patch.object(catalog.ai_http_client, "get", AsyncMock(side_effect=HTTPError("boom"))):
        with pytest.raises(HTTPException) as error:
            await catalog.list_openrouter_models()

    assert error.value.status_code == 502


async def test_resolution_shows_used_models_and_marks_fallbacks(_no_version_bump) -> None:
    """The table must reflect what Sophie actually uses, including the support-tier fallback."""
    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter"))
    # support:filters exists; entertainment has no filters model, so it must fall back to support.
    await catalog.create_model(
        ModelCreate(
            name="support/filter",
            provider="openrouter",
            roles=[AIModelRole(mode="support", purpose=AIModelPurpose.filters)],
        )
    )
    await catalog.create_model(
        ModelCreate(
            name="ent/chat",
            provider="openrouter",
            roles=[AIModelRole(mode="entertainment", purpose=AIModelPurpose.chatbot)],
        )
    )
    await catalog.create_model(
        ModelCreate(
            name="the/summary",
            provider="openrouter",
            roles=[AIModelRole(mode=None, purpose=AIModelPurpose.summary)],
        )
    )
    # A fresh snapshot must be loaded so the just-created roles are visible.
    with patch.object(catalog, "get_catalog", catalog.load_catalog):
        resolution = await catalog.get_resolution()

    assert "disabled" not in resolution.modes
    assert "research" in resolution.purposes
    ent = resolution.per_mode["entertainment"]
    assert ent["chatbot"].model == "ent/chat" and ent["chatbot"].fallback is False
    # No entertainment filters model → the support one answers, marked as a fallback.
    assert ent["filters"].model == "support/filter" and ent["filters"].fallback is True
    # summary is now per-mode too, resolved for every mode.
    assert resolution.per_mode["support"]["summary"].model == "the/summary"


async def test_export_round_trips_through_import(_no_version_bump) -> None:
    await _clear()
    await catalog.create_model(
        ModelCreate(
            name="a/model",
            provider="openrouter",
            roles=[AIModelRole(mode="support", purpose=AIModelPurpose.chatbot)],
        )
    )

    exported = await catalog.export_catalog()
    assert [model.name for model in exported.models] == ["a/model"]

    await _clear()
    result = await catalog.import_catalog(exported)

    assert result.models_created == 1
    stored = await AICatalogModelModel.find_one(AICatalogModelModel.name == "a/model")
    assert stored.roles[0].purpose == AIModelPurpose.chatbot


async def test_merge_import_leaves_models_absent_from_the_file_alone(_no_version_bump) -> None:
    await _clear()
    await catalog.create_model(ModelCreate(name="kept/model", provider="openrouter"))

    incoming = catalog.CatalogExport(models=[catalog.ModelExport(name="new/model", provider="openrouter")])
    result = await catalog.import_catalog(incoming)

    assert result.models_created == 1 and result.deleted == 0
    names = {model.name async for model in AICatalogModelModel.find_all()}
    assert names == {"kept/model", "new/model"}


async def test_replace_import_removes_models_absent_from_the_file(_no_version_bump) -> None:
    await _clear()
    await catalog.create_model(ModelCreate(name="stale/model", provider="openrouter"))
    await catalog.create_model(ModelCreate(name="kept/model", provider="openrouter"))

    incoming = catalog.CatalogExport(models=[catalog.ModelExport(name="kept/model", provider="openrouter")])
    result = await catalog.import_catalog(incoming, replace=True)

    assert result.deleted == 1
    names = {model.name async for model in AICatalogModelModel.find_all()}
    assert names == {"kept/model"}


async def test_an_any_mode_role_serves_every_mode(_no_version_bump) -> None:
    """A (None, chatbot) role is an any-mode default; it must resolve for every mode, not nowhere."""
    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter"))
    await catalog.create_model(
        ModelCreate(
            name="any/chat",
            provider="openrouter",
            roles=[AIModelRole(mode=None, purpose=AIModelPurpose.chatbot)],
        )
    )
    # A mode with its own chatbot model must still win over the any-mode default.
    await catalog.create_model(
        ModelCreate(
            name="support/chat",
            provider="openrouter",
            roles=[AIModelRole(mode="support", purpose=AIModelPurpose.chatbot)],
        )
    )
    with patch.object(catalog, "get_catalog", catalog.load_catalog):
        resolution = await catalog.get_resolution()

    assert resolution.per_mode["entertainment"]["chatbot"].model == "any/chat"
    assert resolution.per_mode["moderation"]["chatbot"].model == "any/chat"
    # The specific role wins over the any-mode one.
    assert resolution.per_mode["support"]["chatbot"].model == "support/chat"
    # An any-mode default is a deliberate choice, not a support-tier fallback.
    assert resolution.per_mode["entertainment"]["chatbot"].fallback is False


async def test_provider_models_queries_an_openai_compatible_endpoint(_no_version_bump) -> None:
    """A custom provider's models come from its own /models, with its own key."""
    await _clear()
    await catalog.create_provider(
        ProviderCreate(
            name="qwencloud",
            kind="openai_compatible",
            base_url="https://example.com/v1",
            api_key="sk-custom",
        )
    )
    payload = {"data": [{"id": "qwen3-vl-flash"}, {"id": "qwen-max"}]}
    response = SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)
    with patch.object(catalog.ai_http_client, "get", AsyncMock(return_value=response)) as get:
        models = await catalog.list_provider_models("qwencloud")

    assert [model.id for model in models] == ["qwen3-vl-flash", "qwen-max"]
    called_url = get.await_args.args[0]
    assert called_url == "https://example.com/v1/models"
    assert get.await_args.kwargs["headers"]["Authorization"] == "Bearer sk-custom"


async def test_provider_models_404s_for_an_unknown_provider(_no_version_bump) -> None:
    await _clear()
    with pytest.raises(HTTPException) as error:
        await catalog.list_provider_models("nope")
    assert error.value.status_code == 404
