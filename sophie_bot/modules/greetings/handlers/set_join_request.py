from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types.base_abc import ParsedArg
from stfu_tg import Doc, Italic, Template

from sophie_bot.db.models import GreetingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.TextWithButtonsArg import TextWithButtonsArg
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(text_with_buttons=TextWithButtonsArg(l_("Content")))
@flags.help(description=l_("Sets join request message."))
class SetJoinRequestMessageHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("setjoinrequest"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        connection = self.connection

        text_with_buttons: dict[str, Any] = self.data["text_with_buttons"]
        raw_text_parsed: ParsedArg[str] | None = text_with_buttons.get("text")
        raw_text = raw_text_parsed.value if raw_text_parsed else None
        text_offset = raw_text_parsed.offset if raw_text_parsed else 0

        raw_buttons = text_with_buttons.get("buttons").value if text_with_buttons.get("buttons") else []
        buttons = ButtonsList.from_ass(raw_buttons)

        saveable = await parse_saveable(self.event, raw_text, offset=text_offset, buttons=buttons)
        await GreetingsModel.change_join_request_message(connection.db_model.iid, saveable)

        doc = Doc(
            Template(_("Join request message was saved in {chat_title}."), chat_title=Italic(connection.title)),
        )

        await self.event.reply(str(doc))


@flags.help(description=l_("Deletes the join request message"))
class DelJoinRequestMessageHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("deljoinrequest"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        connection = self.connection

        db_model = await GreetingsModel.get_by_chat_iid(connection.db_model.iid)
        if not db_model or not db_model.join_request_message:
            return await self.event.reply(
                Template(
                    _("Join request message in {chat_title} has not been set before"), chat_title=connection.title
                ).to_html()
            )

        # Reset to None
        db_model.join_request_message = None
        await db_model.save()

        doc = Doc(
            Template(
                _("Join request message was reset to default in {chat_title}."), chat_title=Italic(connection.title)
            ),
        )

        await self.event.reply(str(doc))
