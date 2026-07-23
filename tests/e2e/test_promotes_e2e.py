"""End-to-end tests for /promote and /demote.

Both call bot.promote_chat_member (captured as an OTHER request carrying the target user_id
and the can_* rights). The post-action admin-cache refresh (get_admins_rights) needs a live
getChatAdministrators the mock bot can't provide, so it is stubbed — it is a side effect, not
the behaviour under test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType

from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id


@pytest.fixture(autouse=True)
def _stub_admin_cache_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.promote.get_admins_rights", AsyncMock())
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.demote.get_admins_rights", AsyncMock())


async def _moderated_group(test_client: TestClient) -> tuple[object, object, object]:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Promote Group")
    await grant_admin(group.id, admin.id, creator=True)
    await grant_bot_admin(group.id)
    target = test_client.create_user(user_id=next_user_id(), first_name="Member", username="promote_member")
    await test_client.send_message(text="init", from_user=target.user, chat=group)
    return admin, group, target.user


def _promote_calls(requests: list, user_id: int) -> list:
    """OTHER requests that carry admin rights for the target — i.e. promote/demote calls."""
    return [
        request
        for request in requests
        if request.request_type == RequestType.OTHER
        and request.params.get("user_id") == user_id
        and "can_delete_messages" in request.params
    ]


@pytest.mark.asyncio
async def test_promote_grants_rights_and_confirms(test_client: TestClient) -> None:
    admin, group, target = await _moderated_group(test_client)

    requests = await test_client.send_command(command="promote", from_user=admin, args=str(target.id), chat=group)

    calls = _promote_calls(requests, target.id)
    assert calls, "promote should call promoteChatMember for the target"
    assert calls[0].params["can_delete_messages"] is True, "A creator delegates the full rights set"
    assert any("promoted successfully" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_promote_with_title(test_client: TestClient) -> None:
    admin, group, target = await _moderated_group(test_client)

    requests = await test_client.send_command(
        command="promote", from_user=admin, args=f"{target.id} Moderator", chat=group
    )

    titles = [
        request
        for request in requests
        if request.request_type == RequestType.OTHER and request.params.get("custom_title") == "Moderator"
    ]
    assert titles, "A title argument should set the admin custom title"


@pytest.mark.asyncio
async def test_promote_requires_promote_rights(test_client: TestClient) -> None:
    _admin, group, target = await _moderated_group(test_client)
    weak_admin = test_client.create_user(user_id=next_user_id(), first_name="WeakAdmin", username="weak_admin")
    await test_client.send_message(text="init", from_user=weak_admin.user, chat=group)
    await grant_admin(group.id, weak_admin.user.id, can_promote_members=False)

    requests = await test_client.send_command(
        command="promote", from_user=weak_admin.user, args=str(target.id), chat=group
    )

    assert not _promote_calls(requests, target.id), "An admin without can_promote_members cannot promote"


@pytest.mark.asyncio
async def test_promote_cannot_target_self(test_client: TestClient) -> None:
    admin, group, _target = await _moderated_group(test_client)

    requests = await test_client.send_command(command="promote", from_user=admin, args=str(admin.id), chat=group)

    assert any("cannot promote yourself" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_demote_removes_rights(test_client: TestClient) -> None:
    admin, group, target = await _moderated_group(test_client)

    requests = await test_client.send_command(command="demote", from_user=admin, args=str(target.id), chat=group)

    calls = [
        request
        for request in requests
        if request.request_type == RequestType.OTHER
        and request.params.get("user_id") == target.id
        and "can_delete_messages" in request.params
    ]
    assert calls, "demote should call promoteChatMember clearing the rights"
    assert calls[0].params["can_delete_messages"] is False, "demote clears the admin rights"
