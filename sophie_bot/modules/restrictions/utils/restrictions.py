from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import ChatMember, ChatMemberMember, ChatMemberRestricted, ChatPermissions
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from sophie_bot.db.models.mute_permissions import MutePermissionsLockModel, MutePermissionsModel
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult
from sophie_bot.utils.logger import log

_RESTRICTION_EXCEPTIONS = (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError)
_TRANSIENT_EXCEPTIONS = (TelegramNetworkError, TelegramRetryAfter, TimeoutError)
_MUTE_PERMISSIONS = ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, False))
_ALL_PERMISSIONS = ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True))


def _permissions_from_member(member: ChatMemberRestricted) -> ChatPermissions:
    return ChatPermissions(**{field_name: getattr(member, field_name) for field_name in ChatPermissions.model_fields})


def _deadline(member: ChatMemberRestricted) -> datetime | None:
    return member.until_date.astimezone(UTC) if member.until_date.timestamp() > 0 else None


def _owns_restriction(snapshot: MutePermissionsModel, member: ChatMember) -> bool:
    return (
        isinstance(member, ChatMemberRestricted)
        and snapshot.applied_permissions is not None
        and _permissions_from_member(member) in (snapshot.applied_permissions, snapshot.pending_permissions)
        and _deadline(member) == snapshot.applied_until
    )


@asynccontextmanager
async def _mute_lock(chat_tid: int, user_tid: int) -> AsyncIterator[None]:
    """A separate lease survives snapshot deletion; Mongo's _id arbitrates workers."""
    collection = MutePermissionsLockModel.get_pymongo_collection()
    lock_id = f"{chat_tid}:{user_tid}"
    token = uuid4().hex
    async with asyncio.timeout(10):
        while True:
            now = datetime.now(UTC)
            try:
                locked = await collection.find_one_and_update(
                    {"_id": lock_id, "lock_until": {"$lte": now}},
                    {"$set": {"lock_until": now + timedelta(seconds=60), "lock_token": token}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                await asyncio.sleep(0.05)
                continue
            if locked is not None:
                break
    try:
        # Bound the operation below the lease duration. A stalled worker cannot
        # continue issuing requests after another worker acquires the expired lease.
        async with asyncio.timeout(45):
            yield
    finally:
        await collection.delete_one({"_id": lock_id, "lock_token": token})


async def _snapshot(bot: Bot, chat_tid: int, user_tid: int) -> tuple[MutePermissionsModel, bool]:
    member = await bot.get_chat_member(chat_id=chat_tid, user_id=user_tid)
    existing = await MutePermissionsModel.find_one({"chat_tid": chat_tid, "user_tid": user_tid})
    if existing is not None and isinstance(member, ChatMemberRestricted) and _owns_restriction(existing, member):
        existing.applied_permissions = _permissions_from_member(member)
        existing.pending_permissions = None
        return existing, False
    if existing is not None:
        await existing.delete()
    restricted = isinstance(member, ChatMemberRestricted)
    snapshot = MutePermissionsModel(
        chat_tid=chat_tid,
        user_tid=user_tid,
        permissions=_permissions_from_member(member) if restricted else None,
        previous_until=_deadline(member) if restricted else None,
        restore_group_defaults=not restricted,
    )
    await snapshot.insert()
    return snapshot, True


async def _restore(bot: Bot, chat_tid: int, user_tid: int, *, expired_only: bool = False) -> bool:
    snapshot = await MutePermissionsModel.find_one({"chat_tid": chat_tid, "user_tid": user_tid})
    if snapshot is None:
        return False
    now = datetime.now(UTC)
    if expired_only and (snapshot.expires_at is None or snapshot.expires_at > now):
        return False
    member = await bot.get_chat_member(chat_id=chat_tid, user_id=user_tid)
    if not isinstance(member, ChatMemberRestricted) or not _owns_restriction(snapshot, member):
        # Legacy snapshots have no ownership proof: leave the current state alone.
        await snapshot.delete()
        log.warning("Discarded superseded mute snapshot", chat_tid=chat_tid, user_tid=user_tid)
        return isinstance(member, ChatMemberMember)
    permissions = snapshot.permissions
    previous_until = snapshot.previous_until
    if snapshot.restore_group_defaults or (previous_until is not None and previous_until <= now):
        permissions = _ALL_PERMISSIONS
        previous_until = None
    elif permissions is not None:
        chat = await bot.get_chat(chat_tid)
        if chat.permissions is None:
            return False
        # Never grant rights removed from the group's defaults since the snapshot.
        permissions = ChatPermissions(
            **{
                field_name: bool(getattr(permissions, field_name)) and bool(getattr(chat.permissions, field_name))
                for field_name in ChatPermissions.model_fields
            }
        )
    if permissions is None:
        return False
    # Telegram interprets deadlines <30s away as forever. A nearly expired old
    # restriction remains until the sweep can safely release it, rather than forever.
    restore_until = previous_until
    if restore_until is not None and restore_until < datetime.now(UTC) + timedelta(seconds=60):
        restore_until = None
    pending_release = previous_until is not None and restore_until is None
    before_restore = snapshot.model_copy(deep=True)
    if pending_release:
        # Persist recovery before an ambiguous Telegram response can leave the
        # restored restriction permanent because its deadline was too close.
        snapshot.pending_permissions = _permissions_from_member(member)
        snapshot.applied_permissions = permissions
        snapshot.applied_until = None
        snapshot.expires_at = previous_until
        snapshot.restore_group_defaults = True
        await snapshot.save()
    try:
        applied = await bot.restrict_chat_member(
            chat_tid,
            user_tid,
            permissions=permissions,
            use_independent_chat_permissions=True,
            until_date=restore_until,
        )
    except _RESTRICTION_EXCEPTIONS:
        if pending_release:
            await before_restore.save()
        raise
    if applied:
        if pending_release:
            snapshot.pending_permissions = None
            await snapshot.save()
        else:
            await snapshot.delete()
    elif pending_release:
        await before_restore.save()
    return applied


async def _apply(bot: Bot, action: RestrictionAction, chat_tid: int, user_tid: int, duration: timedelta | None) -> bool:
    snapshot, created = await _snapshot(bot, chat_tid, user_tid)
    permissions = _MUTE_PERMISSIONS
    if action is RestrictionAction.RESTRICT:
        # A welcome media restriction must not undo the rights captured before CAPTCHA.
        defaults = (await bot.get_chat(chat_tid)).permissions
        if defaults is None:
            if created:
                await snapshot.delete()
            return False
        baseline = snapshot.permissions or defaults
        permissions = ChatPermissions(
            **dict.fromkeys(ChatPermissions.model_fields, False),
        )
        for field_name in (
            "can_send_messages",
            "can_invite_users",
            "can_change_info",
            "can_pin_messages",
            "can_manage_topics",
        ):
            setattr(
                permissions, field_name, bool(getattr(baseline, field_name)) and bool(getattr(defaults, field_name))
            )
    before_apply = snapshot.model_copy(deep=True)
    snapshot.pending_permissions = snapshot.applied_permissions
    snapshot.applied_permissions = permissions
    snapshot.applied_until = None
    snapshot.expires_at = datetime.now(UTC) + duration if duration is not None else None
    snapshot.applied = True
    # Persist the intended state BEFORE Telegram: a timeout or crash can leave the
    # write applied remotely. A retry verifies ownership before restoring anything.
    await snapshot.save()
    try:
        applied = await bot.restrict_chat_member(
            chat_tid,
            user_tid,
            permissions=permissions,
            use_independent_chat_permissions=True,
            until_date=None,
        )
    except _RESTRICTION_EXCEPTIONS:
        if created:
            await snapshot.delete()
        else:
            await before_apply.save()
        raise
    if not applied:
        if created:
            await snapshot.delete()
        else:
            await before_apply.save()
    elif snapshot.pending_permissions is not None:
        snapshot.pending_permissions = None
        await snapshot.save()
    return applied


async def execute_restriction(
    bot: Bot,
    action: RestrictionAction,
    chat_tid: int,
    user_tid: int,
    *,
    until_date: timedelta | None = None,
    expired_only: bool = False,
) -> RestrictionResult:
    try:
        # Serialize all Sophie moderation writes, including CAPTCHA replacement.
        async with _mute_lock(chat_tid, user_tid):
            match action:
                case RestrictionAction.BAN:
                    applied = await bot.ban_chat_member(chat_tid, user_tid, until_date=until_date)
                case RestrictionAction.KICK:
                    applied = await bot.unban_chat_member(chat_tid, user_tid)
                case RestrictionAction.UNBAN:
                    applied = await bot.unban_chat_member(chat_tid, user_tid, only_if_banned=True)
                case RestrictionAction.MUTE | RestrictionAction.RESTRICT:
                    applied = await _apply(bot, action, chat_tid, user_tid, until_date)
                case RestrictionAction.UNMUTE:
                    applied = await _restore(bot, chat_tid, user_tid, expired_only=expired_only)
            if applied and action in (RestrictionAction.BAN, RestrictionAction.KICK, RestrictionAction.UNBAN):
                await MutePermissionsModel.find({"chat_tid": chat_tid, "user_tid": user_tid}).delete()
    except (*_RESTRICTION_EXCEPTIONS, *_TRANSIENT_EXCEPTIONS) as error:
        log.warning(
            "Failed restriction operation", action=action.value, chat_tid=chat_tid, user_tid=user_tid, error=str(error)
        )
        return RestrictionResult(action=action, applied=False)
    return RestrictionResult(action=action, applied=bool(applied))


async def restore_expired_permissions(bot: Bot) -> None:
    # Persistent records recover across scheduler restarts; failed restores remain
    # due and are retried on the next sweep. Recheck the deadline inside the lock.
    async for snapshot in MutePermissionsModel.find({"expires_at": {"$lte": datetime.now(UTC)}}):
        await execute_restriction(
            bot,
            RestrictionAction.UNMUTE,
            snapshot.chat_tid,
            snapshot.user_tid,
            expired_only=True,
        )
