from aiogram.types import Message
from stfu_tg import KeyValue, Template, Title, UserLink
from stfu_tg.doc import Doc, Element

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils.logging import add_offending_message_text
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.shared.actions import ActionDefinition, ModernActionABC, RestrictionAction
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

KICK_ACTION = ActionDefinition[None](
    name="kick_user",
    icon="🚪",
    title=l_("Kick"),
    as_flood=True,
    allow_warns=True,
    skip_for_admins=True,
    restriction_action=RestrictionAction.KICK,
)


class KickModernAction(ModernActionABC[None]):
    definition = KICK_ACTION

    @staticmethod
    def description(data: None) -> Element | str:
        return _("Kicks a user")

    async def handle(self, message: Message, data: dict, filter_data: None) -> Element | None:
        if not message.from_user:
            return

        chat_id = message.chat.id
        reason: str | None = None

        chat_db = data["context"].event_chat
        if chat_db:
            message_text = message.text or message.caption or None
            reason = await generate_restriction_reason(
                chat_db,
                message_text=message_text,
                include_rules=True,
                services=data["services"],
            )

        doc = Doc(
            Title(_("Filter action")),
            Template(
                _("User {user} was automatically kicked based on a filter action"),
                user=UserLink(message.from_user.id, message.from_user.first_name),
            ),
            KeyValue(_("Reason"), reason) if reason else None,
        )

        restriction_result = await execute_restriction(
            data["services"].bot,
            RestrictionAction.KICK,
            chat_id,
            message.from_user.id,
        )
        if not restriction_result.applied:
            return

        if "filter_id" in data:
            details = add_offending_message_text(
                {
                    "target_user_id": message.from_user.id,
                    "filter_id": data["filter_id"],
                    "action": "kick_user",
                },
                message,
            )

            if reason:
                details["reason"] = reason

            await log_event(
                chat_id,
                CONFIG.bot_id,
                LogEvent.USER_KICKED,
                details,
            )

        return doc
