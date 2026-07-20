from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import InlineKeyboardMarkup
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.modules.filters.callbacks import SaveFilterCallback, ToggleFilterSilentCallback


async def _create_filter(test_client: TestClient, *, group_tid: int, user_tid: int, silent: bool):
    group = ChatFactory.create_group(chat_id=group_tid, title="Silent Filters Group")
    user_wrapper = test_client.create_user(user_id=user_tid, first_name="SilentTarget", username="silent_target")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

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
        requests = await test_client.send_message(
            text="this is spam content", from_user=user_wrapper.user, chat=group
        )

    assert requests, "Silent filter should still send its reply"
    schedule_mock.assert_called_once()

    chat_tid, message_ids = schedule_mock.call_args.args
    assert chat_tid == group.id
    # The triggering message plus the bot's reply must both be queued for deletion
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
async def test_editfilter_silent_toggle_persists_to_database(test_client: TestClient) -> None:
    group, user_wrapper, filter_item = await _create_filter(
        test_client, group_tid=-1002700000003, user_tid=927000003, silent=False
    )

    admin_mock = AsyncMock(return_value=True)
    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)

    # The filter middleware only ignores commands for admins, and "/editfilter spam" contains the keyword
    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock),
        patch("sophie_bot.modules.filters.enforce_middleware.is_user_admin", admin_mock),
    ):
        edit_requests = await test_client.send_message(
            text="/editfilter spam", from_user=user_wrapper.user, chat=group
        )
        assert edit_requests, "/editfilter should render the filter confirm screen"

        setup_message = MessageFactory.create(
            text="Filter setup",
            from_user=bot_user,
            chat=group,
            reply_markup=InlineKeyboardMarkup.model_validate(
                {
                    "inline_keyboard": [
                        [{"text": "🔊 Silent mode: off", "callback_data": ToggleFilterSilentCallback().pack()}]
                    ]
                }
            ),
        )

        await test_client.send_callback(
            ToggleFilterSilentCallback().pack(), from_user=user_wrapper.user, message=setup_message
        )
        await test_client.send_callback(
            SaveFilterCallback().pack(), from_user=user_wrapper.user, message=setup_message
        )

    saved = await FiltersModel.get_by_id(filter_item.id)
    assert saved is not None
    assert saved.silent is True, "Toggling silent mode and saving should persist the flag"


@pytest.mark.asyncio
async def test_editfilter_save_does_not_report_filter_as_its_own_duplicate(test_client: TestClient) -> None:
    """Re-saving an edited filter must not trip the duplicate-handler check."""
    group, user_wrapper, filter_item = await _create_filter(
        test_client, group_tid=-1002700000004, user_tid=927000004, silent=False
    )

    admin_mock = AsyncMock(return_value=True)
    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)

    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock),
        patch("sophie_bot.modules.filters.enforce_middleware.is_user_admin", admin_mock),
    ):
        await test_client.send_message(text="/editfilter spam", from_user=user_wrapper.user, chat=group)

        setup_message = MessageFactory.create(text="Filter setup", from_user=bot_user, chat=group)
        save_requests = await test_client.send_callback(
            SaveFilterCallback().pack(), from_user=user_wrapper.user, message=setup_message
        )

    replies = " ".join(request.text or "" for request in save_requests)
    assert "already exists" not in replies, "Editing a filter must not conflict with itself"
    assert "was saved" in replies
