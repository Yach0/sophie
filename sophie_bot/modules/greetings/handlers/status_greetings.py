from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types.base_abc import ParsedArg
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.TextWithButtonsArg import TextWithButtonsArg
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList
from stfu_tg import Doc, Italic, Template

from sophie_bot.db.models import GreetingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(text_with_buttons=TextWithButtonsArg(l_("Content")))
@flags.help(description=l_("Sets welcome message."))
class SetWelcomeMessageHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("setwelcome"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        connection = self.connection
        text_with_buttons: dict[str, Any] = self.data.get("text_with_buttons", {})

        raw_text_parsed: ParsedArg[str] | None = text_with_buttons.get("text")
        raw_text = raw_text_parsed.value if raw_text_parsed else None
        text_offset = raw_text_parsed.offset if raw_text_parsed else 0

        raw_buttons = text_with_buttons.get("buttons").value if text_with_buttons.get("buttons") else []
        buttons = ButtonsList.from_ass(raw_buttons)

        # Workaround for the old syntax
        if raw_text == "off":
            return await self.event.reply(
                str(
                    Template(
                        _("Please the '{cmd}' to control the welcome status."), cmd=Italic("/enablewelcome <on / off>")
                    )
                )
            )

        saveable = await parse_saveable(self.event, raw_text, offset=text_offset, buttons=buttons)
        await GreetingsModel.change_welcome_message(connection.db_model.iid, saveable)

        doc = Doc(
            Template(
                _("Welcome message was successfully updated in {chat_title}."), chat_title=Italic(connection.title)
            ),
            Template(_("Use {cmd} to retrieve the welcome message."), cmd=Italic("/welcome")),
        )
        db_model = await GreetingsModel.get_by_chat_iid(connection.db_model.iid)
        if db_model and db_model.welcome_disabled:
            doc += " "
            doc += Template(
                _(
                    "⚠️ Please note, that the welcome messages are currently disabled in the chat, use '{cmd}' to enable it."
                ),
                cmd=Italic("/enablewelcome on"),
            )

        await self.event.reply(str(doc))
