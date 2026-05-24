from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Optional

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import ChatPermissions

from sophie_bot.services.bot import bot
from sophie_bot.utils.logger import log

_RESTRICTION_EXCEPTIONS = (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError)


async def _execute_restriction(
    chat_tid: int,
    user_tid: int,
    action_name: str,
    coro_factory: Callable[[], Awaitable[bool]],
) -> bool:
    try:
        await coro_factory()
        return True
    except _RESTRICTION_EXCEPTIONS as err:
        log.warning("Failed to %s user", action_name, chat_tid=chat_tid, user_tid=user_tid, error=str(err))
        return False


async def ban_user(chat_tid: int, user_tid: int, until_date: Optional[timedelta] = None) -> bool:
    return await _execute_restriction(
        chat_tid, user_tid, "ban", lambda: bot.ban_chat_member(chat_tid, user_tid, until_date=until_date)
    )


async def kick_user(chat_tid: int, user_tid: int) -> bool:
    return await _execute_restriction(chat_tid, user_tid, "kick", lambda: bot.unban_chat_member(chat_tid, user_tid))


async def mute_user(chat_tid: int, user_tid: int, until_date: Optional[timedelta] = None) -> bool:
    return await _execute_restriction(
        chat_tid,
        user_tid,
        "mute",
        lambda: bot.restrict_chat_member(
            chat_tid,
            user_tid,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_date,
        ),
    )


async def unmute_user(chat_tid: int, user_tid: int) -> bool:
    return await _execute_restriction(
        chat_tid,
        user_tid,
        "unmute",
        lambda: bot.restrict_chat_member(
            chat_tid,
            user_tid,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        ),
    )


async def unban_user(chat_tid: int, user_tid: int) -> bool:
    return await _execute_restriction(
        chat_tid,
        user_tid,
        "unban",
        lambda: bot.unban_chat_member(chat_tid, user_tid, only_if_banned=True),
    )


async def restrict_user(chat_tid: int, user_tid: int, until_date: Optional[timedelta] = None) -> bool:
    return await _execute_restriction(
        chat_tid,
        user_tid,
        "restrict",
        lambda: bot.restrict_chat_member(
            chat_tid,
            user_tid,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
            ),
            until_date=until_date,
        ),
    )
