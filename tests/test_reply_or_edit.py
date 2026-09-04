from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from stfu_tg import Doc

from sophie_bot.modules.utils_.reply_or_edit import reply_or_edit_rich
from sophie_bot.utils.exception import SophieException


def _message() -> Message:
    return Message.model_construct(message_id=1, chat=SimpleNamespace(id=-100), message_thread_id=None)


def _callback() -> CallbackQuery:
    return CallbackQuery.model_construct(message=_message())


@pytest.mark.asyncio
async def test_message_uses_send_rich_message() -> None:
    message = _message()
    with patch("sophie_bot.modules.utils_.reply_or_edit.bot.send_rich_message", AsyncMock(return_value=True)) as send:
        await reply_or_edit_rich(message, Doc("hello"))
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_accessible_callback_uses_rich_edit() -> None:
    callback = _callback()
    with patch("sophie_bot.modules.utils_.reply_or_edit.bot.edit_message_text", AsyncMock(return_value=True)) as edit:
        await reply_or_edit_rich(callback, Doc("hello"))
    edit.assert_awaited_once()
    assert edit.await_args.kwargs["rich_message"].html


@pytest.mark.asyncio
async def test_inaccessible_callback_raises_sophie_exception() -> None:
    callback = CallbackQuery.model_construct(message=InaccessibleMessage.model_construct())
    with pytest.raises(SophieException):
        await reply_or_edit_rich(callback, Doc("hello"))


@pytest.mark.asyncio
async def test_rich_render_failure_does_not_fallback_to_plain_reply() -> None:
    message = _message()
    document = MagicMock()
    document.to_rich.side_effect = RuntimeError("render failed")
    with (
        patch("sophie_bot.modules.utils_.reply_or_edit.bot.send_rich_message", AsyncMock()) as send,
        pytest.raises(RuntimeError, match="render failed"),
    ):
        await reply_or_edit_rich(message, document)
    send.assert_not_awaited()
