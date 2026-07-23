from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.rest.api import auth
from sophie_bot.utils.api import auth as auth_utils
from sophie_bot.modules.ai.api import api_router as ai_api_router
from sophie_bot.modules.rest.api import auth_router
from sophie_bot.services.rest import create_app, init_api_routers

pytestmark = pytest.mark.asyncio


def _app():
    app = create_app()
    init_api_routers(app, [auth_router, ai_api_router])
    return app

_OWNER_TID = -99001
_OPERATOR_TOKEN = "panel-integration-token"


async def _seed_owner() -> None:
    if await ChatModel.get_by_tid(_OWNER_TID):
        return
    await ChatModel(
        tid=_OWNER_TID,
        type=ChatType.private,
        first_name_or_title="Owner",
        username=None,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    ).insert()


async def _operator_client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """A client authenticated exactly the way the panel authenticates: static token → operator JWT."""
    monkeypatch.setattr(auth.CONFIG, "api_operator_token", _OPERATOR_TOKEN)
    monkeypatch.setattr(auth.CONFIG, "owner_id", _OWNER_TID)
    monkeypatch.setattr(auth_utils.CONFIG, "api_jwt_secret", "a" * 40)
    await _seed_owner()

    client = AsyncClient(transport=ASGITransport(app=_app()), base_url="http://panel.test")
    login = await client.post("/auth/login/operator", json={"token": _OPERATOR_TOKEN})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return client


@pytest.mark.usefixtures("db_init")
async def test_the_panel_flow_works_through_the_real_app(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _operator_client(monkeypatch)
    try:
        # The panel loads meta to build its pickers.
        meta = await client.get("/op/ai/catalog/meta")
        assert meta.status_code == 200
        assert "openrouter" in meta.json()["provider_kinds"]

        # Create a provider, then a model tagged with a role, then read them back.
        created = await client.post(
            "/op/ai/catalog/providers",
            json={"name": "openrouter", "kind": "openrouter", "api_key": "sk-integration-1234", "enabled": True},
        )
        assert created.status_code == 201, created.text
        # The wire never carries the plaintext key back.
        assert created.json()["api_key_masked"] == "sk-…1234"
        assert "api_key" not in created.json()

        model = await client.post(
            "/op/ai/catalog/models",
            json={
                "name": "openai/gpt-5.5",
                "provider": "openrouter",
                "roles": [{"mode": None, "purpose": "summary"}],
            },
        )
        assert model.status_code == 201, model.text

        models = await client.get("/op/ai/catalog/models")
        assert [item["name"] for item in models.json()] == ["openai/gpt-5.5"]

        # Update without a key keeps the stored one.
        updated = await client.put("/op/ai/catalog/providers/openrouter", json={"enabled": False})
        assert updated.status_code == 200
        assert updated.json()["has_key"] is True
    finally:
        await client.aclose()


@pytest.mark.usefixtures("db_init")
async def test_the_catalog_is_closed_without_an_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalog holds provider keys, so an unauthenticated caller must be turned away."""
    monkeypatch.setattr(auth.CONFIG, "api_operator_token", _OPERATOR_TOKEN)
    client = AsyncClient(transport=ASGITransport(app=_app()), base_url="http://panel.test")
    try:
        response = await client.get("/op/ai/catalog/providers")
        assert response.status_code in (401, 403)
    finally:
        await client.aclose()
