"""End-to-end tests for /aimoderator: show the picker and cycle a category's detection level."""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_moderator import DetectionLevel
from sophie_bot.modules.ai.callbacks import (
    AIModeratorCategoryCallback,
    AIModeratorConfirmCallback,
    AIModeratorToggleCallback,
)
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory
from sophie_bot.modules.ai.utils.moderation.settings import get_levels, get_moderator_settings
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id


def _callbacks(requests: list) -> list[str]:
    markup_data = next(request.params.get("reply_markup") for request in requests if request.params.get("reply_markup"))
    markup = InlineKeyboardMarkup.model_validate(markup_data)
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_aimoderator_starts_disabled(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="aimoderator", from_user=admin, chat=group)

    callbacks = _callbacks(requests)
    assert len(callbacks) == 1
    assert AIModeratorToggleCallback.unpack(callbacks[0]).action == "enable"


@pytest.mark.asyncio
async def test_enabling_aimoderator_renders_categories_and_confirm(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Enable Group")
    await grant_admin(group.id, admin.id)

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="AI Moderator", from_user=bot_user, chat=group)
    requests = await test_client.send_callback(
        AIModeratorToggleCallback(action="enable").pack(),
        from_user=admin,
        message=picker,
    )

    edit_request = next(request for request in requests if request.request_type == RequestType.EDIT_MESSAGE_TEXT)
    keyboard = InlineKeyboardMarkup.model_validate(edit_request.params["reply_markup"])
    callbacks = [button.callback_data or "" for row in keyboard.inline_keyboard for button in row]
    assert len(callbacks) == len(ModerationCategory) + 2
    assert AIModeratorToggleCallback.unpack(callbacks[0]).action == "disable"
    assert AIModeratorConfirmCallback.unpack(callbacks[-1])

    disabled_requests = await test_client.send_callback(
        AIModeratorToggleCallback(action="disable").pack(),
        from_user=admin,
        message=picker,
    )
    disabled_edit = next(
        request for request in disabled_requests if request.request_type == RequestType.EDIT_MESSAGE_TEXT
    )
    disabled_keyboard = InlineKeyboardMarkup.model_validate(disabled_edit.params["reply_markup"])
    assert [button.text for row in disabled_keyboard.inline_keyboard for button in row] == ["Enable AI Moderator"]


@pytest.mark.asyncio
async def test_confirm_deletes_aimoderator_picker(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Confirm Group")
    await grant_admin(group.id, admin.id)

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="AI Moderator", from_user=bot_user, chat=group)
    requests = await test_client.send_callback(
        AIModeratorConfirmCallback().pack(),
        from_user=admin,
        message=picker,
    )

    assert any(request.request_type == RequestType.DELETE_MESSAGE for request in requests)


@pytest.mark.asyncio
async def test_pressing_a_category_cycles_and_persists_its_level(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Cycle Group")
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="AI Moderator", from_user=bot_user, chat=group)
    await test_client.send_callback(
        AIModeratorToggleCallback(action="enable").pack(),
        from_user=admin,
        message=picker,
    )
    callback_data = AIModeratorCategoryCallback(category=ModerationCategory.PII.value).pack()

    # An unconfigured chat starts at NORMAL, so one press moves it to HIGH and the next wraps to OFF.
    await test_client.send_callback(callback_data, from_user=admin, message=picker)
    levels = get_levels(await get_moderator_settings(chat.iid))
    assert levels[ModerationCategory.PII] == DetectionLevel.HIGH
    assert levels[ModerationCategory.SEXUAL] == DetectionLevel.NORMAL

    await test_client.send_callback(callback_data, from_user=admin, message=picker)
    levels = get_levels(await get_moderator_settings(chat.iid))
    assert levels[ModerationCategory.PII] == DetectionLevel.OFF


@pytest.mark.asyncio
async def test_aimoderator_requires_admin(test_client: TestClient) -> None:
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Auth Group")
    stranger = test_client.create_user(user_id=next_user_id(), first_name="Stranger", username="aimod_stranger")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)

    requests = await test_client.send_command(command="aimoderator", from_user=stranger.user, chat=group)

    assert not any(request.params.get("reply_markup") for request in requests), (
        "A non-admin should not get the category picker"
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_change_a_category(test_client: TestClient) -> None:
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Callback Auth Group")
    stranger = test_client.create_user(user_id=next_user_id(), first_name="Outsider", username="aimod_outsider")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="AI Moderator", from_user=bot_user, chat=group)

    await test_client.send_callback(
        AIModeratorCategoryCallback(category=ModerationCategory.PII.value).pack(),
        from_user=stranger.user,
        message=picker,
    )

    assert await get_moderator_settings(chat.iid) is None, "A non-admin press must not persist anything"
