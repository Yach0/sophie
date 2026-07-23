"""E2E tests for federation ban-list export/import: /fbanlist and /importfbans."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models.federations import FederationTask
from sophie_bot.db.models.federations_enums import FederationTaskType
from sophie_bot.modules.federations.services import FederationBanService
from tests.e2e.federations.conftest import create_federation_via_command
from tests.e2e.helpers import create_test_user_and_group, grant_admin


async def _fed_in_group(test_client: TestClient, *, owner_tid: int, chat_tid: int, fed_name: str):
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client, user_id=owner_tid, chat_id=chat_tid, first_name="ListOwner", group_title=fed_name
    )
    await grant_admin(group.id, owner_user.id, creator=True)
    federation = await create_federation_via_command(test_client, owner_user, group, fed_name, owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)
    return owner_user, group, federation, owner_model


@pytest.mark.asyncio
async def test_fbanlist_empty_federation(test_client: TestClient) -> None:
    owner_user, group, _federation, _model = await _fed_in_group(
        test_client, owner_tid=6100, chat_tid=-1001000006100, fed_name="Empty List Fed"
    )

    requests = await test_client.send_command(command="fbanlist", from_user=owner_user, chat=group)
    assert any("no banned users" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_fbanlist_export_queues_a_task(test_client: TestClient) -> None:
    owner_user, group, federation, owner_model = await _fed_in_group(
        test_client, owner_tid=6101, chat_tid=-1001000006101, fed_name="Export List Fed"
    )
    await FederationBanService.ban_user(federation, 6109, owner_model.iid, reason="spam")

    requests = await test_client.send_command(command="fbanlist", from_user=owner_user, chat=group)
    assert any("export started" in (request.text or "").lower() for request in requests)

    task = await FederationTask.find_one(
        FederationTask.fed_id == federation.fed_id,
        FederationTask.task_type == FederationTaskType.EXPORT,
    )
    assert task is not None, "An export task should be queued"


@pytest.mark.asyncio
async def test_importfbans_without_file_errors(test_client: TestClient) -> None:
    owner_user, group, _federation, _model = await _fed_in_group(
        test_client, owner_tid=6102, chat_tid=-1001000006102, fed_name="Import List Fed"
    )

    requests = await test_client.send_command(command="importfbans", from_user=owner_user, chat=group)
    assert any("csv file" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_fbanlist_rejects_non_admin(test_client: TestClient) -> None:
    _owner, group, _federation, _model = await _fed_in_group(
        test_client, owner_tid=6103, chat_tid=-1001000006103, fed_name="List Auth Fed"
    )
    stranger = test_client.create_user(user_id=6104, first_name="Stranger", username="list_stranger")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)

    requests = await test_client.send_command(command="fbanlist", from_user=stranger.user, chat=group)
    assert any("permission" in (request.text or "").lower() for request in requests)
