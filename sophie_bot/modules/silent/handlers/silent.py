from __future__ import annotations

import json
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from ass_tg.types import OptionalArg, TextArg
from stfu_tg import Bold, Code, Doc, Template

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

TTL_SECONDS = 86400  # 24 hours


def _redis_key(chat_id: int, user_id: int) -> str:
    return f"silent:{chat_id}:{user_id}"


async def _get_state(chat_id: int, user_id: int) -> dict[str, Any]:
    raw = await aredis.get(_redis_key(chat_id, user_id))
    if raw is None:
        return {}
    return json.loads(raw)


async def _set_state(chat_id: int, user_id: int, state: dict[str, Any]) -> None:
    await aredis.set(_redis_key(chat_id, user_id), json.dumps(state), ex=TTL_SECONDS)


async def _delete_stored_messages(chat_id: int, user_id: int) -> None:
    """Delete the previously stored command and reply messages, if any."""
    state = await _get_state(chat_id, user_id)
    for msg_key in ("last_cmd_msg_id", "last_reply_msg_id"):
        msg_id = state.get(msg_key)
        if msg_id is None:
            continue
        try:
            await bot.delete_message(chat_id, msg_id)
        except TelegramBadRequest:
            pass


@flags.help(description=l_("Toggle silent mode — auto-delete your commands and bot replies"))
class SilentHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter("silent"), FeatureFlagFilter("silent_mode"), UserRestricting(admin=True))

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {
            "action": OptionalArg(TextArg(l_("on | off"))),
        }

    async def handle(self) -> Any:
        message: Message = self.event
        action = self.data.get("action")
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else 0

        # Clean up previous messages if silent was enabled
        state = await _get_state(chat_id, user_id)
        if state.get("enabled"):
            await _delete_stored_messages(chat_id, user_id)

        if action is None:
            # Show current status
            enabled = state.get("enabled", False)
            status_text = _("✅ Enabled") if enabled else _("❌ Disabled")
            doc = Doc(
                Bold(_("Silent Mode")),
                status_text,
                "",
                _("Usage:"),
                Code("/silent on"),
                Code("/silent off"),
            )
            await message.reply(doc.to_html())
            return

        action_lower = action.strip().lower()

        if action_lower == "on":
            await _set_state(chat_id, user_id, {"enabled": True})
            await message.reply(_("✅ Silent mode enabled. Your commands and bot replies will be auto-deleted."))
        elif action_lower == "off":
            await aredis.delete(_redis_key(chat_id, user_id))
            await message.reply(_("❌ Silent mode disabled."))
        else:
            doc = Doc(
                Template(_("Unknown argument: {arg}"), arg=Code(action)),
                "",
                _("Usage:"),
                Code("/silent on"),
                Code("/silent off"),
            )
            await message.reply(doc.to_html())
