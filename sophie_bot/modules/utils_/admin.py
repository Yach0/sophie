from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from beanie import PydanticObjectId
from redis.asyncio import Redis

from sophie_bot.config import CONFIG
from sophie_bot.constants import CACHE_ADMIN_TTL_SECONDS, TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.modules.utils_.anonymous_admin import normalize_admin_title
from sophie_bot.modules.utils_.chat_member import update_chat_members
from sophie_bot.utils.logger import log

AdminPermission = Literal[
    "can_post_messages",
    "can_edit_messages",
    "can_delete_messages",
    "can_restrict_members",
    "can_promote_members",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
]
ChatRef = int | PydanticObjectId | ChatModel
REFRESH_MARKER_PREFIX = "admincache:refreshed:"


async def _resolve_model(ref: ChatRef) -> ChatModel | None:
    if isinstance(ref, ChatModel):
        return ref
    if isinstance(ref, int):
        return await ChatModel.get_by_tid(ref)
    return await ChatModel.get_by_iid(ref)


def _is_auto_admin(chat_tid: int, user_tid: int) -> bool:
    return chat_tid == user_tid or user_tid in CONFIG.operators or user_tid == TELEGRAM_ANONYMOUS_ADMIN_BOT_ID


async def get_admin_record(chat: ChatRef, user: ChatRef) -> ChatAdminModel | None:
    chat_model = await _resolve_model(chat)
    user_model = await _resolve_model(user)
    if chat_model is None or user_model is None:
        return None
    return await ChatAdminModel.find_one(
        ChatAdminModel.chat.id == chat_model.iid,
        ChatAdminModel.user.id == user_model.iid,
    )


async def get_chat_admins(chat: ChatRef, *, fetch_links: bool = False) -> list[ChatAdminModel]:
    chat_model = await _resolve_model(chat)
    if chat_model is None:
        return []
    return await ChatAdminModel.find(
        ChatAdminModel.chat.id == chat_model.iid,
        fetch_links=fetch_links,
    ).to_list()


async def get_user_adminships(user: ChatRef, *, fetch_links: bool = False) -> list[ChatAdminModel]:
    user_model = await _resolve_model(user)
    if user_model is None:
        return []
    return await ChatAdminModel.find(
        ChatAdminModel.user.id == user_model.iid,
        fetch_links=fetch_links,
    ).to_list()


async def resolve_anonymous_admin_candidates(chat: ChatRef, title: str) -> list[ChatAdminModel]:
    matched_admins: list[ChatAdminModel] = []
    for admin in await get_chat_admins(chat):
        member_is_anonymous = bool(getattr(admin.member, "is_anonymous", False))
        member_custom_title = normalize_admin_title(getattr(admin.member, "custom_title", None))
        if member_is_anonymous and member_custom_title == title:
            matched_admins.append(admin)
    return matched_admins


def check_member_permissions(
    member: object,
    required_permissions: list[str] | None = None,
    *,
    require_creator: bool = False,
) -> bool | list[str]:
    if require_creator:
        return getattr(member, "status", None) == ChatMemberStatus.CREATOR
    if getattr(member, "status", None) == ChatMemberStatus.CREATOR:
        return True
    if not required_permissions:
        return True
    missing_permissions = [permission for permission in required_permissions if not getattr(member, permission, None)]
    return missing_permissions or True


async def check_user_admin_permissions(
    chat: ChatRef,
    user: ChatRef,
    required_permissions: list[str] | None = None,
    require_creator: bool = False,
) -> bool | list[str]:
    log.debug("check_user_admin_permissions", chat=chat, user=user, permissions=required_permissions)
    if isinstance(chat, int) and isinstance(user, int) and not require_creator and _is_auto_admin(chat, user):
        return True

    chat_model = await _resolve_model(chat)
    user_model = await _resolve_model(user)
    if chat_model is None or user_model is None:
        return False
    if not require_creator and _is_auto_admin(chat_model.tid, user_model.tid):
        return True

    try:
        admin = await get_admin_record(chat_model, user_model)
        if admin is None:
            return False
        return check_member_permissions(
            admin.member,
            required_permissions,
            require_creator=require_creator,
        )
    except TelegramBadRequest as error:
        if "there are no administrators in the private chat" in str(error):
            return False
        raise


async def is_user_admin(chat: ChatRef, user: ChatRef) -> bool:
    return await check_user_admin_permissions(chat, user) is True


async def refresh_admin_snapshot(chat: ChatModel, *, bot: Bot) -> None:
    await update_chat_members(chat, bot=bot)


async def ensure_admin_snapshot(chat: ChatModel, *, bot: Bot, redis: Redis) -> None:
    oldest_admin = await (
        ChatAdminModel.find(ChatAdminModel.chat.id == chat.iid).sort(ChatAdminModel.last_updated).first_or_none()
    )
    if oldest_admin is not None:
        last_updated = oldest_admin.last_updated
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=UTC)
        if (datetime.now(UTC) - last_updated).total_seconds() <= CACHE_ADMIN_TTL_SECONDS:
            return

    claimed = await redis.set(
        f"{REFRESH_MARKER_PREFIX}{chat.iid}",
        "1",
        ex=CACHE_ADMIN_TTL_SECONDS,
        nx=True,
    )
    if not claimed:
        return
    try:
        await refresh_admin_snapshot(chat, bot=bot)
    except TelegramAPIError as error:
        log.warning(
            "AdmincacheMiddleware: Failed to refresh admin cache",
            chat_id=chat.tid,
            error=str(error),
        )


async def get_admins_rights(chat: ChatRef, *, bot: Bot) -> None:
    chat_model = await _resolve_model(chat)
    if chat_model is not None:
        await refresh_admin_snapshot(chat_model, bot=bot)
