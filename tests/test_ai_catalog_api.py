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
    role = AIModelRole(mode="support", purpose=AIModelPurpose.summary)

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
    # The two private-chat modes carry their own roles now, so they are offered too.
    assert "sophie_pm" in meta.modes
    assert "support" in meta.modes
    # Every purpose maps to the flag whose per-chat override pins its model.
    assert meta.model_override_flags["chatbot"] == "ai_chatbot_model"
    assert meta.model_override_flags["moderation_reason"] == "ai_moderation_reason_model"
    assert set(meta.model_override_flags) == set(meta.purposes)


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


async def test_resolution_is_strict_and_scoped_to_each_modes_purposes(_no_version_bump) -> None:
    """The table shows exactly what Sophie resolves: a mode's own role, or nothing, and only the
    purposes that mode can actually use."""
    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter"))
    await catalog.create_model(
        ModelCreate(
            name="ent/chat",
            provider="openrouter",
            roles=[AIModelRole(mode="entertainment", purpose=AIModelPurpose.chatbot)],
        )
    )
    await catalog.create_model(
        ModelCreate(
            name="support/summary",
            provider="openrouter",
            roles=[AIModelRole(mode="support", purpose=AIModelPurpose.summary)],
        )
    )
    # A fresh snapshot must be loaded so the just-created roles are visible.
    with patch.object(catalog, "get_catalog", catalog.load_catalog):
        resolution = await catalog.get_resolution()

    assert "disabled" not in resolution.modes
    assert {"sophie_pm", "sophie_help"} <= set(resolution.modes)
    ent = resolution.per_mode["entertainment"]
    assert ent["chatbot"].model == "ent/chat"
    # No entertainment filters model and no fallback → the cell is present but empty.
    assert ent["filters"].model is None
    # A purpose the mode cannot use is absent entirely (the panel renders it as "—").
    assert "moderation_reason" not in ent
    assert "sophie_inspect" not in ent
    assert resolution.per_mode["support"]["summary"].model == "support/summary"
    # Only the help mode may inspect Sophie's source.
    assert "sophie_inspect" in resolution.per_mode["sophie_help"]
    assert "summary" not in resolution.per_mode["sophie_pm"]


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


async def test_meta_scopes_purposes_to_each_mode(_no_version_bump) -> None:
    """meta.mode_purposes is what the panel greys the role picker and resolution table against."""
    meta = await catalog.get_meta()

    assert set(meta.modes) == set(meta.mode_purposes)
    assert "summary" not in meta.mode_purposes["sophie_pm"]
    assert "filters" not in meta.mode_purposes["sophie_pm"]
    assert meta.mode_purposes["sophie_help"] == ["chatbot", "sophie_inspect"]
    assert "sophie_inspect" not in meta.mode_purposes["support"]


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


async def test_a_role_carries_its_own_service_tier_and_reasoning(_no_version_bump) -> None:
    """The same model can be flex for one role and normal for another."""
    from sophie_bot.modules.ai.utils.ai_catalog import load_catalog, resolve_role

    await _clear()
    await catalog.create_provider(ProviderCreate(name="openrouter", kind="openrouter"))
    await catalog.create_model(
        ModelCreate(
            name="one/model",
            provider="openrouter",
            roles=[
                AIModelRole(mode="support", purpose=AIModelPurpose.research, service_tier="flex", reasoning_effort="high"),
                AIModelRole(mode="support", purpose=AIModelPurpose.chatbot, service_tier="none", reasoning_effort="low"),
            ],
        )
    )
    await load_catalog()

    research = await resolve_role("support", AIModelPurpose.research)
    chatbot = await resolve_role("support", AIModelPurpose.chatbot)

    assert research.model_name == chatbot.model_name == "one/model"
    assert research.service_tier == "flex" and research.reasoning_effort == "high"
    assert chatbot.service_tier == "none" and chatbot.reasoning_effort == "low"


async def test_meta_exposes_service_tiers_and_reasoning_efforts() -> None:
    meta = await catalog.get_meta()
    assert "flex" in meta.service_tiers
    assert meta.reasoning_efforts == ["low", "medium", "high"]
