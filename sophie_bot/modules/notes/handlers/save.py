from typing import Any, Sequence

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types import DividedArg, OptionalArg, SurroundedArg, TextArg, WordArg
from ass_tg.types.base_abc import ParsedArg
from beanie import PydanticObjectId
from bson import Code
from stfu_tg import KeyValue, Section, Template

from sophie_bot.db.models import ChatModel, NoteModel
from sophie_bot.db.models.notes import Saveable
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.TextWithButtonsArg import TextWithButtonsArg
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.SophieButtonABC import AssButtonData
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList
from sophie_bot.modules.notes.utils.names import format_notes_aliases
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(
    notenames=DividedArg(WordArg(l_("Note names"))),
    # note_group=OptionalArg(StartsWithArg("$", WordArg(l_("Group")))),
    description=OptionalArg(SurroundedArg(TextArg(l_("?Description")))),
    text_with_buttons=OptionalArg(TextWithButtonsArg(l_("Content"))),
)
@flags.help(description=l_("Save the note."))
class SaveNote(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("save", "addnote")), UserRestricting(admin=True)

    async def handle(self) -> Any:
        if not self.event.from_user:
            return

        connection: ChatConnection = self.data["connection"]

        text_with_buttons: dict[str, Any] = self.data.get("text_with_buttons", {})
        raw_text_parsed: ParsedArg[str] | None = text_with_buttons.get("text")
        raw_text = raw_text_parsed.value if raw_text_parsed else None
        text_offset = raw_text_parsed.offset if raw_text_parsed else 0

        raw_buttons: list[AssButtonData] = (
            text_with_buttons.get("buttons").value if text_with_buttons.get("buttons") else []
        )
        buttons = ButtonsList.from_ass(raw_buttons)

        notenames: tuple[str, ...] = tuple(name.lower() for name in self.data["notenames"])

        saveable = await parse_saveable(self.event, raw_text, offset=text_offset, buttons=buttons)
        is_created = await self.save(saveable, notenames, connection.db_model.iid, self.event.from_user.id, self.data)

        await self.event.reply(
            str(
                Section(
                    KeyValue("Note names", format_notes_aliases(notenames)),
                    # KeyValue("Group", self.data.get("note_group", "-")),
                    KeyValue("Description", self.data.get("description", "-")),
                    title=_("Note was successfully created") if is_created else _("Note was successfully updated"),
                )
                + Template(
                    _("Use {cmd} to retrieve this note."),
                    cmd=Code(f"#{self.data['notenames'][0]}"),
                )
            )
        )

    async def save(
        self, saveable: Saveable, notenames: Sequence[str], chat_iid: PydanticObjectId, user_id: int, data: dict
    ) -> bool:
        model = await NoteModel.get_by_notenames(chat_iid, notenames)

        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return False

        # Explicitly type the saveable data to ensure type safety
        saveable_dump = saveable.model_dump()
        saveable_data: dict[str, Any] = {
            "chat_tid": chat.tid,
            "names": notenames,
            "note_group": data.get("note_group"),
            "description": data.get("description"),
            "ai_description": False,
            "text": saveable_dump["text"],
            "file": saveable_dump["file"],
            "buttons": saveable_dump["buttons"],
            "parse_mode": saveable_dump["parse_mode"],
            "preview": saveable_dump["preview"],
            "version": saveable_dump["version"],
        }

        if not model:
            model = NoteModel(chat=chat, **saveable_data)
            await model.create()
            await log_event(chat.tid, user_id, LogEvent.NOTE_SAVED, {"note_names": notenames})
            return True

        await model.set(saveable_data)
        await log_event(chat.tid, user_id, LogEvent.NOTE_UPDATED, {"note_names": notenames})
        return False
