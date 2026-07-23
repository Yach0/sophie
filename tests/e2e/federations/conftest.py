"""Shared helpers for federation e2e tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram_test_framework import TestClient

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation

if TYPE_CHECKING:
    from aiogram.types import Chat, User


async def create_federation_via_command(
    test_client: TestClient,
    owner_user: User,
    group: Chat,
    fed_name: str,
    owner_model: ChatModel,
) -> Federation:
    """Create a federation via the /newfed command and return it."""
    await test_client.send_command(command="newfed", from_user=owner_user, args=fed_name, chat=group)
    federation = await Federation.find_one(Federation.fed_name == fed_name)
    assert federation is not None, f"Federation '{fed_name}' should be created"
    assert federation.fed_name == fed_name
    return federation


async def join_chat_to_federation(
    test_client: TestClient,
    user: User,
    group: Chat,
    fed_id: str,
) -> None:
    """Join a chat to a federation via the /joinfed command."""
    await test_client.send_command(command="joinfed", from_user=user, args=fed_id, chat=group)
