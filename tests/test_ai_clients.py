from __future__ import annotations

import pytest

from sophie_bot.db.models.ai.ai_catalog import AICatalogProviderModel, AIProviderKind
from sophie_bot.modules.ai.utils import ai_clients
from sophie_bot.modules.ai.utils.ai_catalog import bump_version, load_catalog
from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client, get_openai_client

pytestmark = pytest.mark.usefixtures("db_init")


async def _store_key(name: str, api_key: str) -> None:
    await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name).delete()
    await AICatalogProviderModel(name=name, kind=AIProviderKind.moderation, api_key=api_key).save()
    await bump_version()
    await load_catalog()


@pytest.fixture(autouse=True)
async def clean_clients():
    ai_clients._clients.clear()
    yield
    ai_clients._clients.clear()
    await AICatalogProviderModel.find_all().delete()
    await load_catalog()


async def test_mistral_client_uses_the_key_from_the_catalog() -> None:
    await _store_key("mistral", "catalog-mistral-key")

    client = await get_mistral_client()

    assert client.sdk_configuration.security.api_key == "catalog-mistral-key"


async def test_openai_client_uses_the_key_from_the_catalog() -> None:
    await _store_key("openai", "catalog-openai-key")

    client = await get_openai_client()

    assert client.api_key == "catalog-openai-key"


async def test_rotating_the_key_builds_a_fresh_client() -> None:
    await _store_key("openai", "first-key")
    first = await get_openai_client()

    await _store_key("openai", "second-key")
    second = await get_openai_client()

    assert first is not second
    assert second.api_key == "second-key"


async def test_same_key_reuses_the_client() -> None:
    await _store_key("openai", "stable-key")

    assert await get_openai_client() is await get_openai_client()


async def test_missing_provider_row_still_builds_a_client() -> None:
    """No row means no key: the request fails with a 401 rather than the bot failing to start."""
    client = await get_openai_client()

    assert client.api_key == ai_clients._MISSING_OPENAI_KEY
