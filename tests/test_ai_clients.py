from __future__ import annotations

import pytest

from sophie_bot.db.models.ai.ai_catalog import AICatalogProviderModel, AIProviderKind
from sophie_bot.modules.ai.utils import ai_clients
from sophie_bot.modules.ai.utils.ai_catalog import bump_version, load_catalog
from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client, get_openai_client

pytestmark = pytest.mark.usefixtures("db_init")


async def _store_key(name: str, api_key: str, *, redis: object) -> None:
    await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name).delete()
    await AICatalogProviderModel(name=name, kind=AIProviderKind.moderation, api_key=api_key).save()
    await bump_version(redis=redis)
    await load_catalog(redis=redis)


@pytest.fixture(autouse=True)
async def clean_clients(test_redis: object):
    ai_clients._clients.clear()
    yield
    ai_clients._clients.clear()
    await AICatalogProviderModel.find_all().delete()
    await load_catalog(redis=test_redis)


async def test_mistral_client_uses_the_key_from_the_catalog(
    test_redis: object,
) -> None:
    await _store_key("mistral", "catalog-mistral-key", redis=test_redis)

    client = await get_mistral_client(redis=test_redis)

    assert client.sdk_configuration.security.api_key == "catalog-mistral-key"


async def test_openai_client_uses_the_key_from_the_catalog(
    test_redis: object,
) -> None:
    await _store_key("openai", "catalog-openai-key", redis=test_redis)

    client = await get_openai_client(redis=test_redis)

    assert client.api_key == "catalog-openai-key"


async def test_rotating_the_key_builds_a_fresh_client(
    test_redis: object,
) -> None:
    await _store_key("openai", "first-key", redis=test_redis)
    first = await get_openai_client(redis=test_redis)

    await _store_key("openai", "second-key", redis=test_redis)
    second = await get_openai_client(redis=test_redis)

    assert first is not second
    assert second.api_key == "second-key"


async def test_same_key_reuses_the_client(test_redis: object) -> None:
    await _store_key("openai", "stable-key", redis=test_redis)

    assert await get_openai_client(
        redis=test_redis
    ) is await get_openai_client(redis=test_redis)


async def test_missing_provider_row_still_builds_a_client(
    test_redis: object,
) -> None:
    """No row means no key: the request fails with a 401 rather than the bot failing to start."""
    client = await get_openai_client(redis=test_redis)

    assert client.api_key == ai_clients._MISSING_OPENAI_KEY
