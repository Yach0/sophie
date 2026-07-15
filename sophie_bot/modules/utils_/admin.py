from __future__ import annotations

from typing import Literal, Optional, Union

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from beanie import PydanticObjectId

from sophie_bot.config import CONFIG
from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.modules.utils_.chat_member import update_chat_members
from sophie_bot.utils.logger import log

# Type alias for admin permissions
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

# A chat or user identified by Telegram ID, DB ID, or an already-resolved model.
ChatRef = Union[int, PydanticObjectId, ChatModel]


async def _resolve_model(ref: ChatRef) -> Optional[ChatModel]:
    if isinstance(ref, ChatModel):
        return ref
    if isinstance(ref, int):
        return await ChatModel.get_by_tid(ref)
    return await ChatModel.get_by_iid(ref)


def _is_auto_admin(chat_tid: int, user_tid: int) -> bool:
    """Return True when a user is implicitly an admin without a DB lookup.

    Covers the user's own PM, bot operators, and the anonymous admin bot
    workaround.
    """
    return chat_tid == user_tid or user_tid in CONFIG.operators or user_tid == TELEGRAM_ANONYMOUS_ADMIN_BOT_ID


async def check_user_admin_permissions(
    chat: ChatRef,
    user: ChatRef,
    required_permissions: Optional[list[str]] = None,
    require_creator: bool = False,
) -> Union[bool, list[str]]:
    """
    Check if a user is an admin in the specified chat and has the required permissions.

    Args:
        chat: Telegram chat ID, Internal DB ID, or a resolved ChatModel
        user: Telegram user ID, Internal DB ID, or a resolved ChatModel
        required_permissions: Optional list of permissions to check (e.g., ["can_restrict_members"])
        require_creator: Require the user to be the chat creator.

    Returns:
        True if the user is an admin with all required permissions.
        A list of missing permission names (list[str]) if any specific permissions are missing.
        False if the user is not an admin at all.
    """
    log.debug("check_user_admin_permissions", chat=chat, user=user, permissions=required_permissions)

    # Must precede resolution: auto-admins (operators, own PM) are granted even
    # when either side has no chat document to resolve.
    if isinstance(chat, int) and isinstance(user, int) and not require_creator:
        if _is_auto_admin(chat, user):
            return True

    chat_model = await _resolve_model(chat)
    if not chat_model:
        return False

    user_model = await _resolve_model(user)
    if not user_model:
        return False

    if not require_creator and _is_auto_admin(chat_model.tid, user_model.tid):
        return True

    # Check database for admin status
    try:
        admin = await ChatAdminModel.find_one(
            ChatAdminModel.chat.id == chat_model.iid,
            ChatAdminModel.user.id == user_model.iid,
        )

        if not admin:
            return False

        if require_creator:
            return admin.member.status == ChatMemberStatus.CREATOR

        # If no specific permissions required, just check admin status
        if not required_permissions:
            return True

        # Chat creator has all permissions
        if admin.member.status == ChatMemberStatus.CREATOR:
            return True

        # Check each required permission
        missing_permissions = []
        for permission in required_permissions:
            permission_value = getattr(admin.member, permission, None)
            if permission_value is None or permission_value is False:
                missing_permissions.append(permission)

        return missing_permissions or True

    except TelegramBadRequest as err:
        # Handle case when function is called outside of a group
        if "there are no administrators in the private chat" in str(err):
            return False
        raise


async def is_user_admin(chat: ChatRef, user: ChatRef) -> bool:
    """
    Check if a user is an admin in the specified chat.

    This is a convenience wrapper around check_user_admin_permissions
    that only checks admin status without specific permissions.

    Args:
        chat: Telegram chat ID, Internal DB ID, or a resolved ChatModel
        user: Telegram user ID, Internal DB ID, or a resolved ChatModel

    Returns:
        True if the user is an admin, False otherwise
    """
    result = await check_user_admin_permissions(chat, user)
    return result is True


async def get_admins_rights(chat: ChatRef) -> None:
    """Refresh admin cache for the chat."""
    chat_model = await _resolve_model(chat)
    if not chat_model:
        return

    await update_chat_members(chat_model)
