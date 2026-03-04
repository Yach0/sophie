from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory


@pytest.mark.asyncio
async def test_warnaction_requires_restrict_admin_rights(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000003, title="Warn Action Permissions")
    user_wrapper = test_client.create_user(user_id=926000003, first_name="RegularUser", username="regular_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    requests = await test_client.send_command(command="warnaction", from_user=user_wrapper.user, chat=group_chat)

    assert requests, "Bot should respond when non-admin uses /warnaction."
    assert any("administrator" in (request.text or "").lower() for request in requests)
