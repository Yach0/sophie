from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, Italic, Template

from sophie_bot.db.models import GreetingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.text_with_buttons_arg import TextWithButtonsArg
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.modules.utils_.text_with_buttons import parse_text_with_buttons
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(text_with_buttons=TextWithButtonsArg(l_("Content")))
@flags.handler_help(description=l_("Sets welcome message."))
class SetWelcomeMessageHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("set_welcome"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        connection = self.connection
        raw_text, text_offset, buttons = parse_text_with_buttons(self.data)

        # Workaround for the old syntax
        if raw_text == "off":
            return await self.event.reply(
                str(
                    Template(
                        _("Please the '{cmd}' to control the welcome status."), cmd=Italic("/enable_welcome <on / off>")
                    )
                )
            )

        saveable = await parse_saveable(
            self.event,
            raw_text,
            offset=text_offset,
            buttons=buttons,
            bot=self.services.bot,
        )
        await GreetingsModel.change_welcome_message(connection.db_model.id, saveable)

        doc = Doc(
            Template(
                _("Welcome message was successfully updated in {chat_title}."), chat_title=Italic(connection.title)
            ),
            Template(_("Use {cmd} to retrieve the welcome message."), cmd=Italic("/welcome")),
        )
        db_model = await GreetingsModel.get_by_chat_iid(connection.db_model.id)
        if db_model and db_model.welcome_disabled:
            doc += " "
            doc += Template(
                _(
                    "⚠️ Please note, that the welcome messages are currently disabled in the chat, use '{cmd}' to enable it."
                ),
                cmd=Italic("/enable_welcome on"),
            )

        await self.event.reply(str(doc))
        return None
