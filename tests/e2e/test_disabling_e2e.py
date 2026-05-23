"""End-to-end tests for the disabling module after manifest handler registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

_ADMIN_PERMS_PATCH = "sophie_bot.filters.admin_rights.check_user_admin_permissions"


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

    with patch(_ADMIN_PERMS_PATCH, new=AsyncMock(return_value=True)):
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
