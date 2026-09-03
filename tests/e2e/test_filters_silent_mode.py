from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.modules.utils_.wizard import WizardCallback
from tests.e2e.helpers import grant_admin


async def _create_filter(test_client: TestClient, *, group_tid: int, user_tid: int, silent: bool, admin: bool = False):
    group = ChatFactory.create_group(chat_id=group_tid, title="Silent Filters Group")
    user_wrapper = test_client.create_user(user_id=user_tid, first_name="SilentTarget", username="silent_target")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    if admin:
        await grant_admin(group.id, user_tid)

    filter_item = FiltersModel(
        chat=chat.iid,
        handler="spam",
        action=None,
        actions={"reply": {"text": "no spam please"}},
        silent=silent,
    )
    await filter_item.insert()
    return group, user_wrapper, filter_item


@pytest.mark.asyncio
async def test_silent_filter_schedules_deletion_of_trigger_and_reply(test_client: TestClient) -> None:
    group, user_wrapper, filter_item = await _create_filter(
        test_client, group_tid=-1002700000001, user_tid=927000001, silent=True
    )

    schedule_mock = MagicMock()
    with (
        patch.object(FiltersModel, "get_filters", AsyncMock(return_value=[filter_item])),
        patch("sophie_bot.modules.filters.enforce_middleware.schedule_message_deletion", schedule_mock),
    ):
        requests = await test_client.send_message(text="this is spam content", from_user=user_wrapper.user, chat=group)

    assert requests
    schedule_mock.assert_called_once()
    chat_tid, message_ids = schedule_mock.call_args.args
    assert chat_tid == group.id
    assert len(message_ids) == 2
    assert schedule_mock.call_args.kwargs["delay_seconds"] == 30


@pytest.mark.asyncio
async def test_non_silent_filter_does_not_schedule_deletion(test_client: TestClient) -> None:
    group, user_wrapper, filter_item = await _create_filter(
        test_client, group_tid=-1002700000002, user_tid=927000002, silent=False
    )

    schedule_mock = MagicMock()
    with (
        patch.object(FiltersModel, "get_filters", AsyncMock(return_value=[filter_item])),
        patch("sophie_bot.modules.filters.enforce_middleware.schedule_message_deletion", schedule_mock),
    ):
        await test_client.send_message(text="this is spam content", from_user=user_wrapper.user, chat=group)

    schedule_mock.assert_not_called()


@pytest.mark.asyncio
async def test_filter_wizard_real_silent_flow_persists_prompt_and_toggle(test_client: TestClient) -> None:
    group = ChatFactory.create_group(chat_id=-1002700000003, title="Silent Wizard Group")
    user_wrapper = test_client.create_user(user_id=927000003, first_name="Admin", username="silent_admin")
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)
    await grant_admin(group.id, user_wrapper.user.id)

    requests = await test_client.send_message(text="/addfilter spam", from_user=user_wrapper.user, chat=group)
    assert requests
    initial_markup = requests[-1].reply_markup
    assert initial_markup is not None
    inline_buttons = [btn for row in initial_markup.get("inline_keyboard", []) for btn in row]
    rich_html = requests[-1].params.get("rich_message", {}).get("html", "")
    assert WizardCallback(scope="filter_action", op="add").pack() in rich_html
    assert any(btn.get("callback_data") == WizardCallback(scope="filter_action", op="cancel").pack() for btn in inline_buttons)
    assert not any(btn.get("callback_data") == WizardCallback(scope="filter_action", op="done").pack() for btn in inline_buttons)

    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    wizard_message = MessageFactory.create(text="Filter setup", from_user=bot_user, chat=group)
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="add").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="select", arg="ai_text").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_message(text="contains crypto scams", from_user=user_wrapper.user, chat=group)
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="toggle", arg="silent").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="done").pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    saved = await FiltersModel.get_by_keyword(chat.iid, "spam")
    assert saved is not None
    assert saved.actions == {"ai_text": {"prompt": "contains crypto scams"}}
    assert saved.silent is True

    state = test_client.dispatcher.fsm.get_context(
        bot=test_client.bot, chat_id=group.id, user_id=user_wrapper.user.id
    )
    fsm_data = await state.get_data()
    assert "wizard" not in fsm_data

@pytest.mark.asyncio
async def test_editfilter_save_does_not_report_filter_as_its_own_duplicate(test_client: TestClient) -> None:
    group, user_wrapper, _filter_item = await _create_filter(
        test_client, group_tid=-1002700000004, user_tid=927000004, silent=False, admin=True
    )
    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)

    await test_client.send_message(text="/editfilter spam", from_user=user_wrapper.user, chat=group)
    setup_message = MessageFactory.create(text="Filter setup", from_user=bot_user, chat=group)
    requests = await test_client.send_callback(
        WizardCallback(scope="filter_action", op="done").pack(),
        from_user=user_wrapper.user,
        message=setup_message,
    )

    messages_html = [
        request.params.get("rich_message", {}).get("html", "")
        for request in requests
        if isinstance(request.params, dict)
    ]
    assert any("was saved" in html for html in messages_html)
    assert not any("already exists" in html for html in messages_html)
