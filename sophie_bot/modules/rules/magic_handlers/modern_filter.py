from aiogram.types import Message
from stfu_tg import Bold, HList, Section, Title
from stfu_tg.doc import Element

from sophie_bot.db.models import RulesModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.shared.actions import ActionResult, ModernActionABC
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class SendRulesAction(ModernActionABC[None]):
    name = "send_rules"

    icon = "🪧"
    title = l_("Send chat rules")
    allow_warns = True

    @staticmethod
    def description(data: None) -> Element | str:
        return _("Replies to the message with the chat rules")

    async def handle(self, message: Message, data: dict, filter_data: None) -> ActionResult | None:
        connection: ChatConnection = data["connection"]

        rules = await RulesModel.get_rules(connection.db_model.iid)

        if not rules:
            return await message.reply(
                Section(_("No rules are set for this chat."), title=_("Rules filter failed")).to_html()
            )

        title = Bold(HList(Title(f"🪧 {_('Rules')}"), _("Filter action")))

        # The rules are always sent as their own message so that fillings, buttons and files
        # get the same treatment as /rules; returning a Doc here would bypass send_saveable.
        # Every sent message is returned so silent filters can clean them up afterwards.
        sent_messages: list[Message] = []
        await common_try(
            send_saveable(
                message,
                message.chat.id,
                rules,
                title=title,
                reply_to=message.message_id,
                connection=connection,
                collect_sent=sent_messages,
            )
        )
        return sent_messages
