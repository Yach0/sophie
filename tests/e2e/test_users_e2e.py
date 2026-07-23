"""End-to-end tests for the users module: /id, /adminlist, /info."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id


@pytest.mark.asyncio
async def test_id_shows_own_and_chat_id(test_client: TestClient) -> None:
    user, group, _model = await create_test_user_and_group(test_client, group_title="Id Group")

    requests = await test_client.send_command(command="id", from_user=user, chat=group)

    combined = " ".join(request.text or "" for request in requests)
    assert str(user.id) in combined, "The command should report the sender's own ID"
    assert str(group.id) in combined, "The command should report the chat ID"


@pytest.mark.asyncio
async def test_adminlist_lists_admins(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(
        test_client, first_name="ChatBoss", group_title="Adminlist Group"
    )
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="adminlist", from_user=admin, chat=group)

    assert any("ChatBoss" in (request.text or "") for request in requests), "The admin should be listed"


@pytest.mark.asyncio
async def test_adminlist_only_in_groups(test_client: TestClient) -> None:
    user = test_client.create_user(user_id=next_user_id(), first_name="Solo", username="solo_user")
    await test_client.send_message(text="init", from_user=user.user)

    requests = await test_client.send_command(command="adminlist", from_user=user.user)

    assert any("only be used in groups" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_info_reports_user_details(test_client: TestClient) -> None:
    viewer, group, _model = await create_test_user_and_group(test_client, group_title="Info Group")
    target = test_client.create_user(user_id=next_user_id(), first_name="Subject", username="subject_user")
    await test_client.send_message(text="init", from_user=target.user, chat=group)

    requests = await test_client.send_command(command="info", from_user=viewer, args=str(target.user.id), chat=group)

    combined = " ".join(request.text or "" for request in requests)
    assert "Subject" in combined
    assert str(target.user.id) in combined


@pytest.mark.asyncio
async def test_info_notes_when_target_is_admin(test_client: TestClient) -> None:
    viewer, group, _model = await create_test_user_and_group(test_client, group_title="Info Admin Group")
    target = test_client.create_user(user_id=next_user_id(), first_name="AdminSubject", username="admin_subject")
    await test_client.send_message(text="init", from_user=target.user, chat=group)
    await grant_admin(group.id, target.user.id)

    requests = await test_client.send_command(command="info", from_user=viewer, args=str(target.user.id), chat=group)

    assert any("admin in this chat" in (request.text or "").lower() for request in requests)
