from collections.abc import Sequence
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import DividedArg, OptionalArg, SurroundedArg, TextArg, WordArg
from ass_tg.types.base_abc import ParsedArg
from beanie import PydanticObjectId
from stfu_tg import Code, KeyValue, Section, Template

from sophie_bot.db.models import ChatModel, NoteModel
from sophie_bot.db.models.notes import Saveable, normalize_notenames
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.metrics.notes import track_note_saved
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_button_abc import AssButtonData
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.text_with_buttons_arg import TextWithButtonsArg
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList
from sophie_bot.modules.notes.utils.names import format_notes_aliases
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.modules.notes.utils.rich import rich_message_has_media
from sophie_bot.utils import flags
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log


@flags.args(
    notenames=DividedArg(WordArg(l_("Note names"))),
    description=OptionalArg(SurroundedArg(TextArg(l_("?Description")))),
    text_with_buttons=OptionalArg(TextWithButtonsArg(l_("Content"))),
)
@flags.help(description=l_("Save the note."))
class SaveNote(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("save", "addnote"), allow_caption=True), UserRestricting(admin=True)

    async def handle(self) -> Any:
        if not self.event.from_user:
            return

        connection: ChatConnection = self.data["connection"]

        text_with_buttons: dict[str, Any] = self.data.get("text_with_buttons", {})
        raw_text_parsed: ParsedArg[str] | None = text_with_buttons.get("text")
        raw_text = raw_text_parsed.value if raw_text_parsed else None
        text_offset = raw_text_parsed.offset if raw_text_parsed else 0

        raw_buttons_parsed: ParsedArg[list[AssButtonData]] | None = text_with_buttons.get("buttons")
        raw_buttons = raw_buttons_parsed.value if raw_buttons_parsed else []
        buttons = ButtonsList.from_ass(raw_buttons)

        notenames: tuple[str, ...] = normalize_notenames(self.data["notenames"])
        if not notenames:
            await self.event.reply(_("Please provide at least one valid note name."))
            return

        # Populated by MediaGroupAggregatorMiddleware when the command is sent on an album.
        album: list[Message] | None = self.data.get("album")

        try:
            saveable = await parse_saveable(
                self.event,
                raw_text,
                offset=text_offset,
                buttons=buttons,
                album=album,
                owner_chat_tid=connection.db_model.tid,
            )
        except SophieException as exc:
            log.warning("SaveNote: validation failed", error="\n".join(str(doc) for doc in exc.docs))
            await self.event.reply("\n".join(str(doc) for doc in exc.docs))
            return
        is_created = await self.save(saveable, notenames, connection.db_model.iid, self.event.from_user.id, self.data)

        track_note_saved(
            has_media=bool(saveable.file or saveable.files or rich_message_has_media(saveable.rich_message)),
            chat_type=self.event.chat.type,
        )

        document = Section(
            KeyValue(_("Note names"), format_notes_aliases(notenames)),
            KeyValue(_("Description"), self.data.get("description", "-")),
            title=_("Note was successfully created") if is_created else _("Note was successfully updated"),
        ) + Template(
            _("Use {cmd} to retrieve this note."),
            cmd=Code(f"#{notenames[0]}"),
        )

        # Replying to an album only captures the single replied-to item, since a reply
        # targets one message. Warn the user and point them at the caption-based flow.
        replied_message = self.event.reply_to_message
        if replied_message and replied_message.media_group_id:
            document += Section(
                Template(
                    _("To save the whole album, send it with {cmd} in the caption instead."),
                    cmd=Code(f"/save {notenames[0]}"),
                ),
                title=_("⚠️ Only the first media of the album was saved"),
            )

        await self.event.reply(str(document))

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
            "files": saveable_dump["files"],
            "buttons": saveable_dump["buttons"],
            "parse_mode": saveable_dump["parse_mode"],
            "preview": saveable_dump["preview"],
            "rich_message": saveable_dump["rich_message"],
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
