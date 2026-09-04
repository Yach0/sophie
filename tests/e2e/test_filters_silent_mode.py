from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.modules.filters.callbacks import FilterManagementCallback, FiltersPageCallback
from sophie_bot.modules.utils_.wizard import WizardCallback
from sophie_bot.utils.feature_flags import set_enabled
from tests.e2e.helpers import get_wizard_session_id, grant_admin


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
    session_id = await get_wizard_session_id(test_client, group.id, user_wrapper.user.id)
    initial_markup = requests[-1].reply_markup
    assert initial_markup is not None
    inline_buttons = [btn for row in initial_markup.get("inline_keyboard", []) for btn in row]
    rich_html = requests[-1].params.get("rich_message", {}).get("html", "")
    assert WizardCallback(scope="filter_action", op="add", session_id=session_id).pack() in rich_html
    assert any(
        btn.get("callback_data")
        == WizardCallback(scope="filter_action", op="cancel", session_id=session_id).pack()
        for btn in inline_buttons
    )
    assert not any(
        btn.get("callback_data")
        == WizardCallback(scope="filter_action", op="done", session_id=session_id).pack()
        for btn in inline_buttons
    )

    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    wizard_message = MessageFactory.create(text="Filter setup", from_user=bot_user, chat=group)
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="add", session_id=session_id).pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(
            scope="filter_action",
            op="select",
            session_id=session_id,
            arg="ai_text",
        ).pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_message(text="contains crypto scams", from_user=user_wrapper.user, chat=group)
    await test_client.send_callback(
        WizardCallback(
            scope="filter_action",
            op="toggle",
            session_id=session_id,
            arg="silent",
        ).pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="done", session_id=session_id).pack(),
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
    session_id = await get_wizard_session_id(test_client, group.id, user_wrapper.user.id)
    setup_message = MessageFactory.create(text="Filter setup", from_user=bot_user, chat=group)
    requests = await test_client.send_callback(
        WizardCallback(scope="filter_action", op="done", session_id=session_id).pack(),
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


@pytest.mark.asyncio
async def test_filter_list_pagination_is_available_to_non_admins(test_client: TestClient) -> None:
    group, user_wrapper, first_filter = await _create_filter(
        test_client,
        group_tid=-1002700000005,
        user_tid=927000005,
        silent=False,
    )
    for filter_index in range(2, 10):
        await FiltersModel(
            chat=first_filter.chat,
            handler=f"filter{filter_index}",
            action=None,
            actions={"reply": {"text": str(filter_index)}},
        ).insert()

    requests = await test_client.send_command(command="filters", from_user=user_wrapper.user, chat=group)
    buttons = [
        button
        for row in (requests[-1].reply_markup or {}).get("inline_keyboard", [])
        for button in row
    ]
    next_callback = next(
        button["callback_data"]
        for button in buttons
        if FiltersPageCallback.unpack(button["callback_data"]).page == 1
    )
    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    list_message = MessageFactory.create(text="Filters", from_user=bot_user, chat=group)

    page_requests = await test_client.send_callback(
        next_callback,
        from_user=user_wrapper.user,
        message=list_message,
    )

    assert any("filter9" in str(request.params) for request in page_requests)


@pytest.mark.asyncio
async def test_filter_list_hides_edit_when_wizard_flag_is_disabled(test_client: TestClient) -> None:
    group, user_wrapper, filter_item = await _create_filter(
        test_client,
        group_tid=-1002700000006,
        user_tid=927000006,
        silent=False,
    )
    await set_enabled("action_config_wizard", False)
    try:
        requests = await test_client.send_command(command="filters", from_user=user_wrapper.user, chat=group)
    finally:
        await set_enabled("action_config_wizard", True)

    rich_html = requests[-1].params.get("rich_message", {}).get("html", "")
    edit_callback = FilterManagementCallback(operation="edit", oid=str(filter_item.id)).pack()
    assert edit_callback not in rich_html


@pytest.mark.asyncio
async def test_filter_wizard_hides_silent_control_when_flag_is_disabled(test_client: TestClient) -> None:
    group = ChatFactory.create_group(chat_id=-1002700000007, title="Silent Control Disabled")
    user_wrapper = test_client.create_user(user_id=927000007, first_name="Admin", username="silent_admin")
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)
    await grant_admin(group.id, user_wrapper.user.id)
    await set_enabled("filters_silent_mode", False)
    try:
        requests = await test_client.send_message(text="/addfilter spam", from_user=user_wrapper.user, chat=group)
        session_id = await get_wizard_session_id(test_client, group.id, user_wrapper.user.id)
    finally:
        await set_enabled("filters_silent_mode", True)

    rich_html = requests[-1].params.get("rich_message", {}).get("html", "")
    silent_callback = WizardCallback(
        scope="filter_action",
        op="toggle",
        session_id=session_id,
        arg="silent",
    ).pack()
    assert silent_callback not in rich_html


@pytest.mark.asyncio
async def test_filter_list_is_silent_when_filters_flag_is_disabled(test_client: TestClient) -> None:
    group, user_wrapper, _filter_item = await _create_filter(
        test_client,
        group_tid=-1002700000009,
        user_tid=927000009,
        silent=False,
    )
    await set_enabled("filters", False)
    try:
        requests = await test_client.send_command(command="filters", from_user=user_wrapper.user, chat=group)
    finally:
        await set_enabled("filters", True)

    assert not requests


@pytest.mark.asyncio
async def test_disabled_wizard_clears_pending_input_instead_of_bypassing_filters(test_client: TestClient) -> None:
    group = ChatFactory.create_group(chat_id=-1002700000008, title="Wizard Input Cleanup")
    user_wrapper = test_client.create_user(user_id=927000008, first_name="Admin", username="cleanup_admin")
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)
    await grant_admin(group.id, user_wrapper.user.id)
    await test_client.send_message(text="/addfilter spam", from_user=user_wrapper.user, chat=group)
    session_id = await get_wizard_session_id(test_client, group.id, user_wrapper.user.id)
    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    wizard_message = MessageFactory.create(text="Filter setup", from_user=bot_user, chat=group)
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="add", session_id=session_id).pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(
            scope="filter_action",
            op="select",
            session_id=session_id,
            arg="ai_text",
        ).pack(),
        from_user=user_wrapper.user,
        message=wizard_message,
    )

    await set_enabled("action_config_wizard", False)
    try:
        requests = await test_client.send_message(
            text="ordinary message",
            from_user=user_wrapper.user,
            chat=group,
        )
    finally:
        await set_enabled("action_config_wizard", True)

    assert any("session has expired" in (request.text or "").lower() for request in requests)
    state = test_client.dispatcher.fsm.get_context(
        bot=test_client.bot,
        chat_id=group.id,
        user_id=user_wrapper.user.id,
    )
    assert "wizard" not in await state.get_data()
    assert await state.get_state() is None
