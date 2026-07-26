"""End-to-end tests for /aimoderator: show the picker and cycle a category's detection level."""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_moderator import DetectionLevel
from sophie_bot.modules.ai.callbacks import AIModeratorCategoryCallback
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory
from sophie_bot.modules.ai.utils.moderation.settings import get_levels, get_moderator_settings
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id


def _callbacks(requests: list) -> list[str]:
    markup_data = next(request.params.get("reply_markup") for request in requests if request.params.get("reply_markup"))
    markup = InlineKeyboardMarkup.model_validate(markup_data)
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_aimoderator_shows_a_button_per_category(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="aimoderator", from_user=admin, chat=group)

    callbacks = _callbacks(requests)
    assert len(callbacks) == len(ModerationCategory)
    assert {AIModeratorCategoryCallback.unpack(data).category for data in callbacks} == {
        category.value for category in ModerationCategory
    }


@pytest.mark.asyncio
async def test_pressing_a_category_cycles_and_persists_its_level(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Cycle Group")
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="AI Moderator", from_user=bot_user, chat=group)
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
