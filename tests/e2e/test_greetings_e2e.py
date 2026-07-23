"""End-to-end tests for Sophie Bot greetings module.

Tests cover:
- /welcome — view welcome settings overview
- /setwelcome — set custom welcome message (admin-only)
- /enablewelcome — toggle welcome on/off (admin-only)
- /cleanservice — toggle service message cleanup (admin-only)
- /cleanwelcome — toggle welcome message cleanup (admin-only)
- /deljoinrequest — delete join request message (admin-only)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory


# ---------------------------------------------------------------------------
# /welcome — view command (no admin required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_welcome_shows_settings(
    test_client: TestClient,
) -> None:
    """The /welcome command shows the welcome settings overview."""

    group = ChatFactory.create_group(chat_id=-1003000000001, title="Greetings E2E Group")
    user_wrapper = test_client.create_user(user_id=930000001, first_name="Viewer", username="viewer_user")

    # Register the chat and user in the DB
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    requests = await test_client.send_command(
        command="welcome",
        from_user=user_wrapper.user,
        chat=group,
    )

    assert requests, "Bot should respond to /welcome command"
    combined_text = " ".join(req.text or "" for req in requests)
    assert "Welcome Settings" in combined_text, f"Response should contain 'Welcome Settings', got: {combined_text}"


# ---------------------------------------------------------------------------
# /enablewelcome — admin-only toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enablewelcome_requires_admin(
    test_client: TestClient,
) -> None:
    """A non-admin user cannot use /enablewelcome."""

    group = ChatFactory.create_group(chat_id=-1003000000002, title="EnableWelcome NoAdmin")
    user_wrapper = test_client.create_user(user_id=930000002, first_name="Regular", username="regular_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    requests = await test_client.send_command(
        command="enablewelcome",
        from_user=user_wrapper.user,
        chat=group,
    )

    assert requests, "Bot should respond when non-admin uses /enablewelcome"
    response_text = requests[-1].text or ""
    assert "administrator" in response_text.lower(), f"Response should mention admin requirement, got: {response_text}"


@pytest.mark.asyncio
async def test_enablewelcome_shows_status(
    test_client: TestClient,
) -> None:
    """An admin calling /enablewelcome without args sees the current status."""

    group = ChatFactory.create_group(chat_id=-1003000000003, title="EnableWelcome Status")
    admin_wrapper = test_client.create_user(user_id=930000003, first_name="Admin", username="admin_enable")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)

    with patch(
        "sophie_bot.filters.admin_rights.check_user_admin_permissions",
        AsyncMock(return_value=True),
    ):
        requests = await test_client.send_command(
            command="enablewelcome",
            from_user=admin_wrapper.user,
            chat=group,
        )

    assert requests, "Bot should respond to /enablewelcome from admin"
    response_text = requests[-1].text or ""
    # StatusBoolHandlerABC shows "Current state" and "Enabled"/"Disabled"
    assert "Current state" in response_text or "Enabled" in response_text or "Disabled" in response_text, (
        f"Response should show current status, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# /setwelcome — admin-only set custom welcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setwelcome_success(
    test_client: TestClient,
) -> None:
    """An admin can set a custom welcome message with /setwelcome."""

    group = ChatFactory.create_group(chat_id=-1003000000004, title="SetWelcome Success")
    admin_wrapper = test_client.create_user(user_id=930000004, first_name="Admin", username="admin_setwelcome")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)

    with patch(
        "sophie_bot.filters.admin_rights.check_user_admin_permissions",
        AsyncMock(return_value=True),
    ):
        requests = await test_client.send_command(
            command="setwelcome",
            from_user=admin_wrapper.user,
            chat=group,
            args="Hello new member!",
        )

    assert requests, "Bot should respond to /setwelcome from admin"
    response_text = requests[-1].text or ""
    assert "successfully updated" in response_text.lower(), f"Response should confirm update, got: {response_text}"


@pytest.mark.asyncio
async def test_setwelcome_requires_admin(
    test_client: TestClient,
) -> None:
    """A non-admin user cannot use /setwelcome."""

    group = ChatFactory.create_group(chat_id=-1003000000005, title="SetWelcome NoAdmin")
    user_wrapper = test_client.create_user(user_id=930000005, first_name="Regular", username="regular_setwelcome")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    requests = await test_client.send_command(
        command="setwelcome",
        from_user=user_wrapper.user,
        chat=group,
        args="Trying to set welcome",
    )

    assert requests, "Bot should respond when non-admin uses /setwelcome"
    response_text = requests[-1].text or ""
    assert "administrator" in response_text.lower(), f"Response should mention admin requirement, got: {response_text}"


# ---------------------------------------------------------------------------
# /cleanservice — admin-only toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanservice_requires_admin(
    test_client: TestClient,
) -> None:
    """A non-admin user cannot use /cleanservice."""

    group = ChatFactory.create_group(chat_id=-1003000000006, title="CleanService NoAdmin")
    user_wrapper = test_client.create_user(user_id=930000006, first_name="Regular", username="regular_cleanservice")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    requests = await test_client.send_command(
        command="cleanservice",
        from_user=user_wrapper.user,
        chat=group,
    )

    assert requests, "Bot should respond when non-admin uses /cleanservice"
    response_text = requests[-1].text or ""
    assert "administrator" in response_text.lower(), f"Response should mention admin requirement, got: {response_text}"


@pytest.mark.asyncio
async def test_cleanservice_shows_status(
    test_client: TestClient,
) -> None:
    """An admin calling /cleanservice without args sees the current status."""

    group = ChatFactory.create_group(chat_id=-1003000000007, title="CleanService Status")
    admin_wrapper = test_client.create_user(user_id=930000007, first_name="Admin", username="admin_cleanservice")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)

    with patch(
        "sophie_bot.filters.admin_rights.check_user_admin_permissions",
        AsyncMock(return_value=True),
    ):
        requests = await test_client.send_command(
            command="cleanservice",
            from_user=admin_wrapper.user,
            chat=group,
        )

    assert requests, "Bot should respond to /cleanservice from admin"
    response_text = requests[-1].text or ""
    assert "Current state" in response_text or "Enabled" in response_text or "Disabled" in response_text, (
        f"Response should show current status, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# /cleanwelcome — admin-only toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanwelcome_shows_status(
    test_client: TestClient,
) -> None:
    """An admin calling /cleanwelcome without args sees the current status."""

    group = ChatFactory.create_group(chat_id=-1003000000008, title="CleanWelcome Status")
    admin_wrapper = test_client.create_user(user_id=930000008, first_name="Admin", username="admin_cleanwelcome")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)

    with patch(
        "sophie_bot.filters.admin_rights.check_user_admin_permissions",
        AsyncMock(return_value=True),
    ):
        requests = await test_client.send_command(
            command="cleanwelcome",
            from_user=admin_wrapper.user,
            chat=group,
        )

    assert requests, "Bot should respond to /cleanwelcome from admin"
    response_text = requests[-1].text or ""
    assert "Current state" in response_text or "Enabled" in response_text or "Disabled" in response_text, (
        f"Response should show current status, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# /deljoinrequest — admin-only delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deljoinrequest_no_message_set(
    test_client: TestClient,
) -> None:
    """Deleting a join request message when none is set returns an appropriate error."""

    group = ChatFactory.create_group(chat_id=-1003000000009, title="DelJoinReq None")
    admin_wrapper = test_client.create_user(user_id=930000009, first_name="Admin", username="admin_deljoinreq")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)

    with patch(
        "sophie_bot.filters.admin_rights.check_user_admin_permissions",
        AsyncMock(return_value=True),
    ):
        requests = await test_client.send_command(
            command="deljoinrequest",
            from_user=admin_wrapper.user,
            chat=group,
        )

    assert requests, "Bot should respond to /deljoinrequest from admin"
    response_text = requests[-1].text or ""
    assert "has not been set" in response_text.lower(), (
        f"Response should indicate no message was set, got: {response_text}"
    )
