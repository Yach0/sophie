from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import ChatMember, ChatPermissions
from pymongo.errors import DuplicateKeyError

from sophie_bot.db.models.mute_permissions import MutePermissionsModel
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
_MUTE_LOCKS: defaultdict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)


def _permissions_from_member(member: ChatMember) -> ChatPermissions:
    permission_values = {
        field_name: getattr(member, field_name)
        for field_name in ChatPermissions.model_fields
        if hasattr(member, field_name)
    }
    if permission_values:
        return ChatPermissions(**permission_values)
    return ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True))


async def _get_or_create_snapshot(
    bot: Bot,
    chat_tid: int,
    user_tid: int,
) -> tuple[MutePermissionsModel, bool]:
    member = await bot.get_chat_member(chat_id=chat_tid, user_id=user_tid)
    permissions = _permissions_from_member(member)
    existing = await MutePermissionsModel.find_one(
        MutePermissionsModel.chat_tid == chat_tid,
        MutePermissionsModel.user_tid == user_tid,
    )
    if existing:
        return existing, False

    snapshot = MutePermissionsModel(chat_tid=chat_tid, user_tid=user_tid, permissions=permissions)
    try:
        await snapshot.insert()
    except DuplicateKeyError:
        existing = await MutePermissionsModel.find_one(
            MutePermissionsModel.chat_tid == chat_tid,
            MutePermissionsModel.user_tid == user_tid,
        )
        if existing is None:
            raise
        return existing, False
    return snapshot, True


async def execute_restriction(
    bot: Bot,
    action: RestrictionAction,
    chat_tid: int,
    user_tid: int,
    *,
    until_date: timedelta | None = None,
) -> RestrictionResult:
    mute_lock = (
        _MUTE_LOCKS[(chat_tid, user_tid)] if action in (RestrictionAction.MUTE, RestrictionAction.UNMUTE) else None
    )
    if mute_lock is not None:
        await mute_lock.acquire()

    snapshot: MutePermissionsModel | None = None
    snapshot_created = False
    try:
        match action:
            case RestrictionAction.BAN:
                await bot.ban_chat_member(chat_tid, user_tid, until_date=until_date)
            case RestrictionAction.KICK:
                await bot.unban_chat_member(chat_tid, user_tid)
            case RestrictionAction.MUTE:
                snapshot, snapshot_created = await _get_or_create_snapshot(bot, chat_tid, user_tid)
                await bot.restrict_chat_member(
                    chat_tid,
                    user_tid,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_date,
                )
                snapshot.applied = True
                await snapshot.save()
            case RestrictionAction.UNBAN:
                await bot.unban_chat_member(chat_tid, user_tid, only_if_banned=True)
            case RestrictionAction.UNMUTE:
                snapshot = await MutePermissionsModel.find_one(
                    MutePermissionsModel.chat_tid == chat_tid,
                    MutePermissionsModel.user_tid == user_tid,
                    MutePermissionsModel.applied == True,
                )
                if snapshot is None:
                    return RestrictionResult(action=action, applied=False)
                await bot.restrict_chat_member(
                    chat_tid,
                    user_tid,
                    permissions=snapshot.permissions,
                )
                await snapshot.delete()
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
        if action is RestrictionAction.MUTE and snapshot is not None and snapshot_created:
            await snapshot.delete()
        log.warning(
            "Failed to %s user",
            _ACTION_LOG_NAMES[action],
            chat_tid=chat_tid,
            user_tid=user_tid,
            error=str(error),
        )
        return RestrictionResult(action=action, applied=False)
    finally:
        if mute_lock is not None:
            mute_lock.release()
    return RestrictionResult(action=action, applied=True)
