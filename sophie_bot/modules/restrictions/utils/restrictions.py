from __future__ import annotations

from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import ChatPermissions

from sophie_bot.shared.actions import RestrictionAction, RestrictionResult
from sophie_bot.utils.logger import log

_RESTRICTION_EXCEPTIONS = (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError)
_ACTION_LOG_NAMES: dict[RestrictionAction, str] = {
    RestrictionAction.BAN: "ban",
    RestrictionAction.KICK: "kick",
    RestrictionAction.MUTE: "mute",
    RestrictionAction.UNBAN: "unban",
    RestrictionAction.UNMUTE: "unmute",
    RestrictionAction.RESTRICT: "restrict",
}


async def execute_restriction(
    bot: Bot,
    action: RestrictionAction,
    chat_tid: int,
    user_tid: int,
    *,
    until_date: timedelta | None = None,
) -> RestrictionResult:
    try:
        match action:
            case RestrictionAction.BAN:
                await bot.ban_chat_member(chat_tid, user_tid, until_date=until_date)
            case RestrictionAction.KICK:
                await bot.unban_chat_member(chat_tid, user_tid)
            case RestrictionAction.MUTE:
                await bot.restrict_chat_member(
                    chat_tid,
                    user_tid,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_date,
                )
            case RestrictionAction.UNBAN:
                await bot.unban_chat_member(chat_tid, user_tid, only_if_banned=True)
            case RestrictionAction.UNMUTE:
                await bot.restrict_chat_member(
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
                        can_invite_users=True,
                    ),
                )
            case RestrictionAction.RESTRICT:
                await bot.restrict_chat_member(
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
                )
    except _RESTRICTION_EXCEPTIONS as error:
        log.warning(
            "Failed to %s user",
            _ACTION_LOG_NAMES[action],
            chat_tid=chat_tid,
            user_tid=user_tid,
            error=str(error),
        )
        return RestrictionResult(action=action, applied=False)
    return RestrictionResult(action=action, applied=True)
