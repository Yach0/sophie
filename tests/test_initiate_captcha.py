from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendPhoto

from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityConfirmCB, WelcomeSecurityMoveCB
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import CaptchaDMBlockedError, initiate_captcha


@pytest.mark.asyncio
async def test_initiate_captcha_raises_when_user_blocked() -> None:
    user = SimpleNamespace(tid=123456)
    group = SimpleNamespace(id="group_iid", tid=-100987654321, first_name_or_title="Test Group")
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=user.tid, user_id=user.tid))

    forbidden_error = TelegramForbiddenError(
        method=SendPhoto(chat_id=user.tid, photo="placeholder"),
        message="Forbidden: bot was blocked by the user",
    )

    async def blocked_send(**_kwargs: Any) -> None:
        assert await state.get_state() == WelcomeSecurityFSM.captcha.state
        state_data = await state.get_data()
        assert state_data["ws_chat_iid"] == str(group.id)
        assert state_data["ws_is_join_request"] is True
        assert "captcha" in state_data
        raise forbidden_error

    send_photo = AsyncMock(side_effect=blocked_send)

    bot = SimpleNamespace(send_photo=send_photo)
    dispatcher = SimpleNamespace(fsm=SimpleNamespace(get_context=Mock(return_value=state)))

    with pytest.raises(CaptchaDMBlockedError):
        await initiate_captcha(
            user,
            group,
            is_join_request=True,
            bot=bot,
            dispatcher=dispatcher,
        )

    assert send_photo.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("is_join_request", [False, True])
async def test_initiate_captcha_presents_the_persisted_target(is_join_request: bool) -> None:
    user = SimpleNamespace(tid=123456)
    group = SimpleNamespace(id="group_iid", tid=-100987654321, first_name_or_title="Group <Friends>")
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=user.tid, user_id=user.tid))
    sent_message = SimpleNamespace(message_id=10)

    async def send_photo(**kwargs: Any) -> SimpleNamespace:
        assert await state.get_state() == WelcomeSecurityFSM.captcha.state
        state_data = await state.get_data()
        assert state_data["ws_chat_iid"] == str(group.id)
        assert state_data["ws_is_join_request"] is is_join_request
        assert state_data["captcha"]["base_emoji"] in kwargs["caption"]
        assert "Group &lt;Friends&gt;" in kwargs["caption"]
        assert "Incorrect solution" not in kwargs["caption"]
        assert kwargs["chat_id"] == user.tid
        assert kwargs["photo"].filename == "captcha.jpeg"
        assert kwargs["photo"].data

        markup = kwargs["reply_markup"]
        for button, direction in zip(markup.inline_keyboard[0], ("left", "right"), strict=True):
            movement = WelcomeSecurityMoveCB.unpack(button.callback_data)
            assert movement.direction == direction
            assert movement.chat_iid == state_data["ws_chat_iid"]
            assert movement.is_join_request is is_join_request
        confirm = WelcomeSecurityConfirmCB.unpack(markup.inline_keyboard[1][0].callback_data)
        assert confirm.chat_iid == state_data["ws_chat_iid"]
        assert confirm.is_join_request is is_join_request
        return sent_message

    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=send_photo))
    dispatcher = SimpleNamespace(fsm=SimpleNamespace(get_context=Mock(return_value=state)))
    result = await initiate_captcha(user, group, is_join_request, bot=bot, dispatcher=dispatcher)

    assert result is sent_message
