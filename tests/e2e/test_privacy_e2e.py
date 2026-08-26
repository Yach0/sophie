from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.types import Chat, Message, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.request_capture import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.modules.privacy import PrivacyMenuCallback
from tests.e2e.helpers import next_user_id


@pytest.mark.asyncio
async def test_privacy_command_replies_with_policy_link(test_client: TestClient) -> None:
    user = test_client.create_user(user_id=next_user_id(), first_name="Privacy", username="privacy_command")

    requests = await user.send_command("privacy")

    replies = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert len(replies) == 1
    assert replies[0].reply_markup["inline_keyboard"][0][0]["url"] == CONFIG.privacy_link


@pytest.mark.asyncio
async def test_privacy_callback_edits_source_message(test_client: TestClient) -> None:
    user_wrapper = test_client.create_user(
        user_id=next_user_id(),
        first_name="Privacy",
        username="privacy_callback",
    )
    start_message = Message(
        message_id=295,
        date=datetime.now(UTC),
        chat=Chat(id=user_wrapper.user.id, type="private", first_name=user_wrapper.user.first_name),
        from_user=User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie"),
        text="Start menu",
    )

    requests = await test_client.send_callback(
        PrivacyMenuCallback(back_to_start=True).pack(),
        from_user=user_wrapper.user,
        message=start_message,
    )

    edits = [request for request in requests if request.request_type == RequestType.EDIT_MESSAGE_TEXT]
    assert len(edits) == 1
    assert edits[0].reply_markup["inline_keyboard"][0][0]["url"] == CONFIG.privacy_link
    assert edits[0].reply_markup["inline_keyboard"][1][0]["callback_data"] == "go_to_start"
    assert not [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
