from datetime import UTC, datetime, timedelta

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from beanie import PydanticObjectId
from stfu_tg import Doc, Element, Section, Template, Title

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_connection_settings import ChatConnectionSettingsModel
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.modules.connections.utils.constants import CONNECTION_DISCONNECT_TEXT
from sophie_bot.modules.connections.utils.texts import CONNECTION_OBSOLETE_NOTICE
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


async def set_connected_chat(user_tid: int, chat_tid: int | None):
    """
    Connects user to a chat.
    If chat_tid is None, disconnects.
    Sets expiry to 48 hours from now.
    """
    # Clear legacy redis cache just in case
    await aredis.delete(f"connection_cache_{user_tid}")

    user = await ChatModel.get_by_tid(user_tid)
    if not user:
        log.error("set_connected_chat: user not found", user_tid=user_tid)
        return

    if chat_tid is None:
        if conn := await ChatConnectionModel.get_by_user_iid(user.iid):
            conn.chat = None
            conn.expires_at = None
            await conn.save()
        return

    chat = await ChatModel.get_by_tid(chat_tid)
    if not chat:
        log.error("set_connected_chat: chat not found", chat_tid=chat_tid)
        return

    expires_at = datetime.now(UTC) + timedelta(hours=48)

    conn = await ChatConnectionModel.get_by_user_iid(user.iid)
    if conn:
        conn.chat = chat
        conn.expires_at = expires_at
        if chat.iid not in [history_chat.to_ref().id for history_chat in conn.history]:
            conn.history.append(chat)
        await conn.save()
    else:
        conn = ChatConnectionModel(user=user, chat=chat, expires_at=expires_at, history=[chat])
        await conn.insert()


async def check_connection_permissions(chat_iid: PydanticObjectId, user_iid: PydanticObjectId) -> bool:
    """
    Checks if a user is allowed to connect to a chat.
    Admins are always allowed.
    Normal users are allowed if 'allow_users_connect' is enabled in settings.
    """
    # Admins always allowed
    if await is_user_admin(chat_iid, user_iid):
        return True

    # Check settings
    settings = await ChatConnectionSettingsModel.get_by_chat_iid(chat_iid)
    return not settings or settings.allow_users_connect


async def get_connection_text(chat_id: int) -> Doc:
    """Returns the formatted document for a successful connection."""
    chat = await ChatModel.get_by_tid(chat_id)
    obsolete_notice: str | Element | None = (
        CONNECTION_OBSOLETE_NOTICE if await is_enabled("connection_webapp_notice") else None
    )

    return Doc(
        Title(_("Connected!")),
        Template(_("Connected to {chat_name}."), chat_name=chat.first_name_or_title if chat else str(chat_id)),
        Section(
            _("Notices"),
            obsolete_notice,
            _("⏳ This connection will last for 48 hours."),
        ),
    )


def get_disconnect_markup() -> ReplyKeyboardMarkup:
    """Returns the reply keyboard markup with the disconnect button."""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=str(CONNECTION_DISCONNECT_TEXT))]], resize_keyboard=True)
