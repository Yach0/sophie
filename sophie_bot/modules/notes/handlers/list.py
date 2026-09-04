from __future__ import annotations

import time
from secrets import token_urlsafe
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from ass_tg.types import OptionalArg, TextArg
from beanie import PydanticObjectId
from pydantic import BaseModel, ValidationError
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
_NOTES_LISTS_KEY = "notes_lists"
_NOTES_LIST_TTL_SECONDS = 15 * 60
_MAX_NOTES_LISTS = 8


class _NotesListContext(BaseModel):
    search: str | None
    chat_iid: PydanticObjectId
    chat_tid: int
    chat_title: str
    started_at: float


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
            return await self._reply_or_send(_empty_notes_text(search, self.connection.title))

        list_id: str | None = None
        if state is not None:
            list_id = await _store_notes_context(
                state,
                _NotesListContext(
                    search=search,
                    chat_iid=self.connection.db_model.iid,
                    chat_tid=self.connection.tid,
                    chat_title=self.connection.title or _("Unknown"),
                    started_at=time.time(),
                ),
            )
        page = paginate(notes, _PAGE_SIZE)
        return await self._reply_or_send(
            _notes_page_text(self.connection.title, search, page),
            _notes_navigation(page, list_id),
        )


class NotesPageHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (NotesPageCallback.filter(),)

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        callback_data: NotesPageCallback = self.data["callback_data"]
        context = await _load_notes_context(self.state, callback_data.list_id)
        if context is None:
            await callback.answer(_("This list has expired. Please run the command again."), show_alert=True)
            return

        notes = await _query_notes(context.chat_iid, context.chat_tid, context.search)
        if not notes:
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(_empty_notes_text(context.search, context.chat_title))
            await _remove_notes_context(self.state, callback_data.list_id)
            await callback.answer()
            return

        page = paginate(notes, _PAGE_SIZE, callback_data.page)
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(
                _notes_page_text(context.chat_title, context.search, page),
                reply_markup=_notes_navigation(page, callback_data.list_id),
            )
        await callback.answer()


async def _store_notes_context(state: Any, context: _NotesListContext) -> str:
    data = await state.get_data()
    raw_contexts = data.get(_NOTES_LISTS_KEY)
    contexts = dict(raw_contexts) if isinstance(raw_contexts, dict) else {}
    active_contexts: list[tuple[str, _NotesListContext]] = []
    for list_id, raw_context in contexts.items():
        try:
            parsed_context = _NotesListContext.model_validate(raw_context)
        except ValidationError:
            continue
        if time.time() - parsed_context.started_at <= _NOTES_LIST_TTL_SECONDS:
            active_contexts.append((list_id, parsed_context))
    active_contexts.sort(key=lambda item: item[1].started_at)
    contexts = {
        list_id: active_context.model_dump(mode="json")
        for list_id, active_context in active_contexts[-(_MAX_NOTES_LISTS - 1) :]
    }
    list_id = token_urlsafe(6)
    contexts[list_id] = context.model_dump(mode="json")
    await state.update_data(**{_NOTES_LISTS_KEY: contexts})
    return list_id


async def _load_notes_context(state: Any, list_id: str) -> _NotesListContext | None:
    data = await state.get_data()
    raw_contexts = data.get(_NOTES_LISTS_KEY)
    if not isinstance(raw_contexts, dict):
        return None
    try:
        context = _NotesListContext.model_validate(raw_contexts.get(list_id))
    except ValidationError:
        return None
    if time.time() - context.started_at > _NOTES_LIST_TTL_SECONDS:
        await _remove_notes_context(state, list_id)
        return None
    return context


async def _remove_notes_context(state: Any, list_id: str) -> None:
    data = await state.get_data()
    raw_contexts = data.get(_NOTES_LISTS_KEY)
    if not isinstance(raw_contexts, dict):
        return
    contexts = dict(raw_contexts)
    contexts.pop(list_id, None)
    data[_NOTES_LISTS_KEY] = contexts
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


def _notes_navigation(page: PaginationPage[NoteModel], list_id: str | None) -> InlineKeyboardMarkup | None:
    if list_id is None:
        return None
    buttons = build_pagination_row(
        page,
        lambda page_number: NotesPageCallback(list_id=list_id, page=page_number).pack(),
    )
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
