"""E2E tests for the federation log channel: /fsetlog + /funsetlog."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.modules.federations.services import FederationManageService
from tests.e2e.federations.conftest import create_federation_via_command
from tests.e2e.helpers import create_test_user_and_group, grant_admin


async def _fed_in_group(test_client: TestClient, *, owner_tid: int, chat_tid: int, fed_name: str):
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client, user_id=owner_tid, chat_id=chat_tid, first_name="LogOwner", group_title=fed_name
    )
    await grant_admin(group.id, owner_user.id, creator=True)
    federation = await create_federation_via_command(test_client, owner_user, group, fed_name, owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)
    return owner_user, group, federation


@pytest.mark.asyncio
async def test_fsetlog_then_funsetlog(test_client: TestClient) -> None:
    owner_user, group, federation = await _fed_in_group(
        test_client, owner_tid=6080, chat_tid=-1001000006080, fed_name="Log Fed"
    )

    set_requests = await test_client.send_command(command="fsetlog", from_user=owner_user, chat=group)
    assert any("log channel" in (request.text or "").lower() for request in set_requests)

    with_log = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert with_log is not None and with_log.log_chat is not None, "The log channel should be recorded"

    unset_requests = await test_client.send_command(command="funsetlog", from_user=owner_user, chat=group)
    assert any("removed" in (request.text or "").lower() for request in unset_requests)

    without_log = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert without_log is not None and without_log.log_chat is None, "The log channel should be cleared"


@pytest.mark.asyncio
async def test_fsetlog_rejects_non_owner(test_client: TestClient) -> None:
    _owner, group, federation = await _fed_in_group(
        test_client, owner_tid=6082, chat_tid=-1001000006082, fed_name="Log Auth Fed"
    )
    intruder = test_client.create_user(user_id=6083, first_name="Intruder", username="log_intruder")
    await test_client.send_message(text="init", from_user=intruder.user, chat=group)

    requests = await test_client.send_command(command="fsetlog", from_user=intruder.user, chat=group)
    assert any("owner" in (request.text or "").lower() for request in requests)

    unchanged = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert unchanged is not None and unchanged.log_chat is None
