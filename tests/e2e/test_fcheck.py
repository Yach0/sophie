from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation, FederationBan


@pytest.mark.asyncio
async def test_fcheck_pm_full_option(test_client: TestClient) -> None:
    user_id = 123456789
    creator_id = 987654321
    group_id = -1001234567890
    now = datetime.now(timezone.utc)

    user = test_client.create_user(user_id=user_id, first_name="Test", username="testuser")

    user_db = ChatModel(
        tid=user_id,
        type=ChatType.private,
        first_name_or_title="Test",
        last_name=None,
        username="testuser",
        is_bot=False,
        last_saw=now,
    )
    await user_db.insert()

    creator_db = ChatModel(
        tid=creator_id,
        type=ChatType.private,
        first_name_or_title="Creator",
        last_name=None,
        username="creator",
        is_bot=False,
        last_saw=now,
    )
    await creator_db.insert()

    group_db = ChatModel(
        tid=group_id,
        type=ChatType.group,
        first_name_or_title="Test Group",
        last_name=None,
        username=None,
        is_bot=False,
        last_saw=now,
    )
    await group_db.insert()

    federation_one = Federation(fed_name="Fed One", fed_id="fed-one", creator=creator_db, chats=[group_db])
    await federation_one.insert()

    federation_two = Federation(fed_name="Fed Two", fed_id="fed-two", creator=creator_db, chats=[group_db])
    await federation_two.insert()

    ban_one = FederationBan(
        fed_id=federation_one.fed_id,
        user_id=user_id,
        banned_chats=[group_db],
        time=now,
        by=creator_db,
        reason="Spam",
    )
    await ban_one.insert()

    ban_two = FederationBan(
        fed_id=federation_two.fed_id,
        user_id=user_id,
        banned_chats=[],
        time=now,
        by=creator_db,
        reason="Other",
    )
    await ban_two.insert()

    await user.send_command("fcheck")
    last_message = user.get_last_message()
    assert last_message is not None, "Bot should respond to /fcheck"
    response_text = last_message.text or ""
    assert "Fed One" in response_text, "Filtered list should include bans with banned chats"
    assert "Fed Two" not in response_text, "Filtered list should exclude bans without banned chats"

    await user.send_command("fcheck full")
    last_message = user.get_last_message()
    assert last_message is not None, "Bot should respond to /fcheck full"
    response_text = last_message.text or ""
    assert "Fed One" in response_text, "Full list should include bans with banned chats"
    assert "Fed Two" in response_text, "Full list should include bans without banned chats"
