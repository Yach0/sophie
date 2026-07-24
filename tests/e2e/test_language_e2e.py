"""End-to-end tests for /lang: show the picker and persist the selection."""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.cache.locale import get_selected_locale
from sophie_bot.db.models import ChatModel
from sophie_bot.modules.language.handlers.language import SelectLangCb
from sophie_bot.services.i18n import i18n
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id


def _some_locale() -> str:
    """A real available locale other than the default, to switch to."""
    choices = [code for code in i18n.available_locales if code != i18n.default_locale]
    assert choices, "The test i18n should expose more than one locale"
    return choices[0]


@pytest.mark.asyncio
async def test_lang_shows_the_picker(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Lang Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="lang", from_user=admin, chat=group)

    markup_data = next(request.params.get("reply_markup") for request in requests if request.params.get("reply_markup"))
    markup = InlineKeyboardMarkup.model_validate(markup_data)
    callbacks = [button.callback_data or "" for row in markup.inline_keyboard for button in row]
    assert any(data.startswith("set_lang") for data in callbacks), "The picker should offer language buttons"


@pytest.mark.asyncio
async def test_selecting_a_language_persists(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Lang Select Group")
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    target_locale = _some_locale()
    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="Select a language", from_user=bot_user, chat=group)

    await test_client.send_callback(SelectLangCb(code=target_locale).pack(), from_user=admin, message=picker)

    assert await get_selected_locale(chat.iid) == target_locale


@pytest.mark.asyncio
async def test_lang_requires_admin(test_client: TestClient) -> None:
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="Lang Auth Group")
    stranger = test_client.create_user(user_id=next_user_id(), first_name="Stranger", username="lang_stranger")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)

    requests = await test_client.send_command(command="lang", from_user=stranger.user, chat=group)

    assert any("administrator" in (request.text or "").lower() for request in requests)
