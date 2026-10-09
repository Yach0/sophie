"""End-to-end tests for Sophie Bot.

These tests use aiogram-test-framework to simulate user interactions
with the bot in a fully mocked environment (MongoDB via mongomock, Redis via fakeredis).

Note: aiogram-test-framework primarily supports private chat testing.
Group chat testing would require manual message construction.
"""

from __future__ import annotations

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram_test_framework import TestClient

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.error.handlers.error import SophieErrorHandler
from tests.e2e.helpers import create_test_user_and_group


@pytest.mark.asyncio
async def test_start_command_creates_chat(test_client: TestClient) -> None:
    """Test that /start command creates a ChatModel entry.

    This test verifies:
    1. The bot responds to /start command
    2. A ChatModel is created in the database for the user
    3. The response contains expected text
    """
    # Create a mock user
    user_id = 123456789
    user = test_client.create_user(user_id=user_id, first_name="Test", username="testuser")

    # Send /start command
    await user.send_command("start")

    # Verify the bot responded (check if any message was received)
    last_message = user.get_last_message()
    assert last_message is not None, "Bot should respond to /start command"

    # Verify the response contains expected text
    response_text = last_message.text or ""
    assert "Sophie" in response_text, f"Response should mention bot name, got: {response_text}"

    # Verify a ChatModel was created in the database
    chat = await ChatModel.find_one(ChatModel.tid == user_id)
    assert chat is not None, "ChatModel should be created in database"
    assert chat.tid == user_id


@pytest.mark.asyncio
async def test_help_command(test_client: TestClient) -> None:
    """Test that /help command works correctly.

    This test verifies:
    1. The bot responds to /help command
    2. The response contains help information
    """
    # Create a mock user
    user = test_client.create_user(user_id=111222333, first_name="Test", username="testuser")

    # Send /help command
    requests = await user.send_command("help")

    # The help menu is sent as a rich message, so its text lives in the rich payload.
    assert requests, "Bot should respond to /help command"

    last_request = requests[-1]
    rendered = str(last_request.params.get("rich_message") or last_request.text or "")
    assert len(rendered) > 0, "Help response should not be empty"


@pytest.mark.asyncio
async def test_id_command(test_client: TestClient) -> None:
    """Test that /id command returns user and chat ID.

    This test verifies:
    1. The bot responds to /id command
    2. The response contains the user ID
    """
    # Create a mock user
    user_id = 555666777
    user = test_client.create_user(user_id=user_id, first_name="TestUser", username="testuser")

    # Send /id command
    await user.send_command("id")

    # Get the last message
    last_message = user.get_last_message()
    assert last_message is not None, "Bot should respond to /id command"

    # Verify the response contains the user ID
    response_text = last_message.text or ""
    assert str(user_id) in response_text, f"Response should contain user ID {user_id}, got: {response_text}"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_message", ["message to be replied not found", "REPLY_MESSAGE_ID_INVALID"])
async def test_group_start_delivers_help_when_command_was_deleted(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error_message: str,
) -> None:
    user, group, _model = await create_test_user_and_group(test_client)
    original_make_request = test_client.bot.session.make_request
    rejected: list[SendMessage] = []
    reported: list[Exception] = []

    def capture_error(error: Exception) -> None:
        reported.append(error)

    monkeypatch.setattr(SophieErrorHandler, "capture_sentry", staticmethod(capture_error))

    async def make_request(bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None) -> TelegramType:
        if isinstance(method, SendMessage) and method.chat_id == group.id and method.reply_parameters:
            rejected.append(method)
            raise TelegramBadRequest(method=method, message=error_message)
        return await original_make_request(bot, method, timeout=timeout)

    monkeypatch.setattr(test_client.bot.session, "make_request", make_request)
    requests = await test_client.send_command(command="start", from_user=user, chat=group)

    assert not reported
    delivered = [request for request in requests if request.params.get("reply_markup")]
    assert len(rejected) == 1
    assert len(delivered) == 1
    response = delivered[0]
    assert response.chat_id == group.id
    assert not response.params.get("reply_parameters")
