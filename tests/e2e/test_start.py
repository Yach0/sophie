"""End-to-end tests for Sophie Bot.

These tests use aiogram-test-framework to simulate user interactions
with the bot in a fully mocked environment (MongoDB via mongomock, Redis via fakeredis).

Note: aiogram-test-framework primarily supports private chat testing.
Group chat testing would require manual message construction.
"""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models.chat import ChatModel


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
    await user.send_command("help")

    # Get the last message
    last_message = user.get_last_message()
    assert last_message is not None, "Bot should respond to /help command"

    # Verify the response contains expected content
    response_text = last_message.text or ""
    # Help should mention modules or commands
    assert len(response_text) > 0, "Help response should not be empty"


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
