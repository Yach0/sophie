from typing import Optional

from aiogram.types import Message
from stfu_tg import KeyValue, Template, Title, UserLink
from stfu_tg.doc import Doc, Element

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.filters.types.modern_action_abc import ModernActionABC
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils import is_user_admin, kick_user
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log


class KickModernAction(ModernActionABC[None]):
    name = "kick_user"
    icon = "🚪"
    title = l_("Kick")
    as_flood = True
    allow_warns = True

    @staticmethod
    def description(data: None) -> Element | str:
        return _("Kicks a user")

    async def handle(self, message: Message, data: dict, filter_data: None) -> Optional[Element]:
        if not message.from_user:
            return

        chat_id = message.chat.id
        user_id = message.from_user.id
        reason: Optional[str] = None

        chat_db = data.get("chat_db")
        if chat_db:
            message_text = message.text or message.caption or None
            reason = await generate_restriction_reason(chat_db, message_text=message_text, include_rules=True)

        if await is_user_admin(chat_id, user_id):
            log.debug("KickModernAction: user is admin, skipping...")
            return

        doc = Doc(
            Title(_("Filter action")),
            Template(
                _("User {user} was automatically kicked based on a filter action"),
                user=UserLink(message.from_user.id, message.from_user.first_name),
            ),
            KeyValue(_("Reason"), reason) if reason else None,
        )

        if not await kick_user(chat_id, message.from_user.id):
            return

        if "filter_id" in data:
            details: dict[str, str | int] = {
                "target_user_id": message.from_user.id,
                "filter_id": data["filter_id"],
                "action": "kick_user",
            }

            if reason:
                details["reason"] = reason

            await log_event(
                chat_id,
                CONFIG.bot_id,
                LogEvent.USER_KICKED,
                details,
            )

        return doc
