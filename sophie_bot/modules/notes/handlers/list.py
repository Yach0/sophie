from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from ass_tg.types import OptionalArg, TextArg
from stfu_tg import Code, Doc, Italic, KeyValue, Section, Template

from sophie_bot.db.models import NoteModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.utils.ai_quota import check_quota
from sophie_bot.modules.notes.callbacks import NotesPageCallback
from sophie_bot.modules.notes.utils.list import format_notes_list
from sophie_bot.modules.notes.utils.semantic_search import semantic_search_notes
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils import flags
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.pagination import PaginationPage, build_pagination_row, paginate

LIST_CMDS = ("notes", "saved", "notelist")
_PAGE_SIZE = 8
_NOTES_LIST_KEY = "notes_list"


@flags.args(search=OptionalArg(TextArg(l_("?Search notes"))))
@flags.help(description=l_("Lists available notes."))
@flags.disableable(name="notes")
class NotesList(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(LIST_CMDS),)

    async def _reply_or_send(self, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> Any:
        async def send_message() -> Any:
            return await self.bot.send_message(
                chat_id=self.event.chat.id,
                text=text,
                message_thread_id=self.event.message_thread_id,
                reply_markup=reply_markup,
            )

        return await common_try(self.event.reply(text, reply_markup=reply_markup), reply_not_found=send_message)

    async def handle(self) -> Any:
        search: str | None = self.data.get("search")
        state = self.data.get("state")
        notes = await _query_notes(self.connection.db_model.iid, self.connection.tid, search)
        if not notes:
            if state is not None:
                await _clear_notes_context(state)
            return await self._reply_or_send(_empty_notes_text(search, self.connection.title))

        if state is not None:
            await state.update_data(**{_NOTES_LIST_KEY: {"search": search}})
        page = paginate(notes, _PAGE_SIZE)
        return await self._reply_or_send(_notes_page_text(self.connection.title, search, page), _notes_navigation(page))


class NotesPageHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (NotesPageCallback.filter(),)

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        context = await _load_notes_context(self.state)
        if context is None:
            await callback.answer(_("This list has expired. Please run the command again."), show_alert=True)
            return
        search = context.get("search")
        if search is not None and not isinstance(search, str):
            await callback.answer(_("This list has expired. Please run the command again."), show_alert=True)
            return

        notes = await _query_notes(self.connection.db_model.iid, self.connection.tid, search)
        if not notes:
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(_empty_notes_text(search, self.connection.title))
            await _clear_notes_context(self.state)
            await callback.answer()
            return

        page = paginate(notes, _PAGE_SIZE, self.data["callback_data"].page)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                _notes_page_text(self.connection.title, search, page),
                reply_markup=_notes_navigation(page),
            )
        await callback.answer()


async def _load_notes_context(state: Any) -> dict[str, Any] | None:
    data = await state.get_data()
    context = data.get(_NOTES_LIST_KEY)
    return dict(context) if isinstance(context, dict) else None


async def _clear_notes_context(state: Any) -> None:
    data = await state.get_data()
    data.pop(_NOTES_LIST_KEY, None)
    await state.set_data(data)


async def _query_notes(chat_iid: Any, chat_tid: int, search: str | None) -> list[NoteModel]:
    rag_allowed = (
        search
        and await is_enabled("notes_rag_list_search", chat_tid=chat_tid)
        and await is_enabled("ai_chatbot", chat_tid=chat_tid)
    )
    if rag_allowed:
        quota_result = await check_quota(chat_iid)
        rag_allowed = quota_result.allowed
    if rag_allowed:
        assert search is not None
        return await semantic_search_notes(chat_iid, search)
    notes = await NoteModel.get_chat_notes(chat_iid)
    return [note for note in notes if not search or any(search in name for name in note.names)]


def _empty_notes_text(search: str | None, chat_title: str) -> str:
    if search:
        return str(
            Template(
                _("No notes found by the provided search pattern {pattern} in {chat_name}."),
                pattern=Italic(search),
                chat_name=Italic(chat_title),
            )
        )
    return str(Template(_("No notes found in {chat_name}."), chat_name=Italic(chat_title)))


def _notes_page_text(chat_title: str, search: str | None, page: PaginationPage[NoteModel]) -> str:
    page_notes = list(page.items)
    doc = Doc(
        Section(
            KeyValue(_("Search pattern"), Italic(search)) if search else None,
            format_notes_list(page_notes),
            title=Template(_("Notes in {chat_name}"), chat_name=chat_title).to_html(),
        ),
        " ",
        Template(
            _("Use {cmd} to retrieve a note. Example: {cmd_example}"),
            cmd=Italic(_("#(Note name)")),
            cmd_example=Code(f"#{page_notes[0].names[0]}"),
        ),
    )
    return str(doc)


def _notes_navigation(page: PaginationPage[NoteModel]) -> InlineKeyboardMarkup | None:
    buttons = build_pagination_row(page, lambda page_number: NotesPageCallback(page=page_number).pack())
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
