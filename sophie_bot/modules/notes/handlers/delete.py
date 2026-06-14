from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types import DividedArg, WordArg
from stfu_tg import HList, KeyValue, Section, Template, VList

from sophie_bot.db.models import NoteModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.metrics.notes import track_note_deleted
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.notes.utils.names import format_notes_aliases
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(notenames=DividedArg(WordArg(l_("Note name"))))
@flags.help(description=l_("Deletes notes."))
class DelNote(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("delnote", "clear")), UserRestricting(admin=True)

    async def handle(self) -> Any:
        if not self.event.from_user:
            return

        chat: ChatConnection = self.data["connection"]

        raw_notenames: list[str] = self.data["notenames"]

        deleted: list[NoteModel] = []
        not_found: list[str] = []

        for raw_name in raw_notenames:
            if note := await NoteModel.get_by_notenames(chat.db_model.iid, (raw_name,)):
                await note.delete()
                deleted.append(note)
                await log_event(chat.tid, self.event.from_user.id, LogEvent.NOTE_DELETED, {"note_names": note.names})
                track_note_deleted(chat_type=self.event.chat.type)
            else:
                not_found.append(raw_name)

        if not deleted:
            return await self.event.reply(
                str(Template(_("No notes were found with the specified names in {chat}."), chat=chat.title))
            )

        await self.event.reply(
            str(
                Section(
                    KeyValue(_("Chat"), chat.title),
                    KeyValue(
                        (_("Name") if len(deleted[0].names) == 1 else _("Names")),
                        format_notes_aliases(deleted[0].names),
                    )
                    if len(deleted) == 1
                    else Section(
                        HList(
                            *[format_notes_aliases(note.names) for note in deleted],
                        ),
                        title="Deleted notes",
                    ),
                    Section(VList(*not_found), title=_("Not found")) if not_found else None,
                    title=_("Note was successfully deleted", "Notes were successfully deleted", len(deleted)),
                )
            )
        )
