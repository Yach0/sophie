"""End-to-end tests for the disabling module after manifest handler registration."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.disabling import DisablingModel
from tests.e2e.helpers import grant_admin


@pytest.mark.asyncio
async def test_disable_command_persists(test_client: TestClient) -> None:
    """`/disable <cmd>` records the command in DisablingModel."""

    group = ChatFactory.create_group(chat_id=-1002800000020, title="Disable Persist Group")
    admin_wrapper = test_client.create_user(user_id=928000020, first_name="Admin", username="disable_admin")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)
    await grant_admin(group.id, admin_wrapper.user.id)

    requests = await test_client.send_command(command="disable", from_user=admin_wrapper.user, args="rules", chat=group)

    assert any("disabled" in (request.text or "").lower() for request in requests)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    assert "rules" in await DisablingModel.get_disabled(chat.iid), "The disabled command should be persisted"


@pytest.mark.asyncio
async def test_disableable_lists_commands(test_client: TestClient) -> None:
    """Any user can list disable-able commands."""
    group_chat = ChatFactory.create_group(chat_id=-1002800000001, title="Disableable Group")
    user_wrapper = test_client.create_user(user_id=928000001, first_name="Member", username="member_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    requests = await test_client.send_command(
        command="disableable",
        from_user=user_wrapper.user,
        chat=group_chat,
    )

    assert requests, "Bot should respond to /disableable"
    assert any("Disable-able" in (response.text or "") for response in requests)


@pytest.mark.asyncio
async def test_disabled_lists_empty_for_admin(test_client: TestClient) -> None:
    """Admin sees empty disabled list when nothing is disabled."""
    group_chat = ChatFactory.create_group(chat_id=-1002800000002, title="Disabled Empty Group")
    admin_wrapper = test_client.create_user(user_id=928000002, first_name="Admin", username="admin_user")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    requests = await test_client.send_command(
        command="disabled",
        from_user=admin_wrapper.user,
        chat=group_chat,
    )

    assert requests, "Bot should respond to /disabled"
    assert any("No disabled commands" in (response.text or "") for response in requests)


@pytest.mark.asyncio
async def test_disabled_requires_admin(test_client: TestClient) -> None:
    """Non-admin cannot list disabled commands."""
    group_chat = ChatFactory.create_group(chat_id=-1002800000003, title="Disabled Perms Group")
    user_wrapper = test_client.create_user(user_id=928000003, first_name="Regular", username="regular_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    requests = await test_client.send_command(
        command="disabled",
        from_user=user_wrapper.user,
        chat=group_chat,
    )

    assert requests, "Bot should respond when non-admin uses /disabled"
    assert any("administrator" in (response.text or "").lower() for response in requests)
