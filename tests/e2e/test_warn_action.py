from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.modules.utils_.wizard import WizardCallback
from sophie_bot.utils.feature_flags import set_enabled
from tests.e2e.helpers import grant_admin


@pytest.mark.asyncio
async def test_warnaction_requires_restrict_admin_rights(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000003, title="Warn Action Permissions")
    user_wrapper = test_client.create_user(user_id=926000003, first_name="RegularUser", username="regular_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    requests = await test_client.send_command(command="warnaction", from_user=user_wrapper.user, chat=group_chat)

    assert requests, "Bot should respond when non-admin uses /warnaction."
    assert any("administrator" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_warnaction_is_silent_when_wizard_flag_is_disabled(test_client: TestClient) -> None:
    await set_enabled("action_config_wizard", False)
    try:
        group_chat = ChatFactory.create_group(chat_id=-1002600000005, title="Warn Action Disabled")
        user_wrapper = test_client.create_user(user_id=926000005, first_name="WarnAdmin", username="warn_admin")
        await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
        await grant_admin(group_chat.id, user_wrapper.user.id)

        requests = await test_client.send_command(command="warnaction", from_user=user_wrapper.user, chat=group_chat)
        assert not requests
    finally:
        await set_enabled("action_config_wizard", True)


@pytest.mark.asyncio
async def test_warnaction_configures_each_warn_action_through_wizard(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000004, title="Warn Action Wizard")
    user_wrapper = test_client.create_user(user_id=926000004, first_name="WarnAdmin", username="warn_admin")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, user_wrapper.user.id)
    await test_client.send_command(command="warnaction", from_user=user_wrapper.user, chat=group_chat)

    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    wizard_message = MessageFactory.create(text="Warn actions", from_user=bot_user, chat=group_chat)
    await test_client.send_callback(
        WizardCallback(scope="warn_action_each", op="open").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(scope="warn_action_each", op="add").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(scope="warn_action_each", op="select", arg="kick_user").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(scope="warn_action_each", op="done").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )

    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None
    settings = await WarnSettingsModel.get_by_chat_iid(chat.iid)
    assert settings is not None
    assert [action.name for action in settings.on_each_warn_actions] == ["kick_user"]


@pytest.mark.asyncio
async def test_warnaction_wizard_parent_back_returns_overview(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000006, title="Warn Action Back")
    user_wrapper = test_client.create_user(user_id=926000006, first_name="WarnAdmin", username="warn_admin")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, user_wrapper.user.id)
    await test_client.send_command(command="warnaction", from_user=user_wrapper.user, chat=group_chat)

    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    wizard_message = MessageFactory.create(text="Warn actions", from_user=bot_user, chat=group_chat)
    await test_client.send_callback(
        WizardCallback(scope="warn_action_each", op="open").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    requests = await test_client.send_callback(
        WizardCallback(scope="warn_action_each", op="back").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )

    assert requests
    messages_html = [
        request.params.get("rich_message", {}).get("html", "")
        for request in requests
        if isinstance(request.params, dict)
    ]
    assert any("Warn actions" in html or "warn_action" in html for html in messages_html)
