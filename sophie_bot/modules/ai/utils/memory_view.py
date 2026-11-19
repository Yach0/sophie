from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from pydantic import BaseModel
from stfu_tg import (
    Code,
    Doc,
    Heading,
    KeyValue,
    ListItem,
    OrderedList,
    Paragraph,
    RichBlockQuote,
    Template,
)

from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.db.models.ai.ai_evidence_memory import MemoryFact, MemorySource, MemoryState
from sophie_bot.modules.ai.callbacks import AIMemoryPageCallback, MemoryListView
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.pagination import build_pagination_footer, build_pagination_row, paginate

_BUTTONS_PER_ROW = 8
_PAGE_SIZE = 2 * _BUTTONS_PER_ROW
_PREVIEW_LENGTH = 600
# Measure serialized HTML to leave room under Telegram's 32,768-character rich-text limit.
_MAX_RICH_PAGE_BYTES = 30_000


class MemoryMember(BaseModel):
    tid: int
    name: str


class MemoryBrowser(BaseModel):
    owner_tid: int
    search: str = ""
    member: MemoryMember | None = None


def _preview(text: str, limit: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _preview_list(entries: Sequence[MemoryFact | MemorySource]) -> OrderedList:
    return OrderedList(*(ListItem(_preview(entry.text, _PREVIEW_LENGTH)) for entry in entries))


def _list_header(data: AIMemoryPageCallback, browser: MemoryBrowser) -> Doc:
    if browser.member is not None:
        return Doc(Heading(Template(_("AI memory for {user}"), user=_preview(browser.member.name, 120))))
    title = {"show": _("Your AI memory"), "history": _("Memory history"), "sources": _("Your source messages")}
    header = Doc(Heading(title[data.view]))
    if browser.search and data.view != "sources":
        header += Paragraph(Template(_("Search: {text}"), text=_preview(browser.search, 120)))
    return header


def _list_pages(
    entries: Sequence[MemoryFact | MemorySource], header: Doc, status: str
) -> list[list[MemoryFact | MemorySource]]:
    pages: list[list[MemoryFact | MemorySource]] = []
    items: list[MemoryFact | MemorySource] = []
    footer = build_pagination_footer(len(entries) - 1, len(entries))
    status_block = Paragraph(status) if status else ""
    for entry in entries:
        candidate = [*items, entry]
        if items and (
            len(items) == _PAGE_SIZE
            or len(Doc(header, _preview_list(candidate), status_block, footer).to_rich().encode("utf-8"))
            > _MAX_RICH_PAGE_BYTES
        ):
            pages.append(items)
            items = []
        items.append(entry)
    if items:
        pages.append(items)
    return pages


def _memory_list(
    data: AIMemoryPageCallback, entries: Sequence[MemoryFact | MemorySource], browser: MemoryBrowser, excluded: bool
) -> tuple[Doc, InlineKeyboardMarkup]:
    document = _list_header(data, browser)
    status = _("Memory capture is off for you.") if excluded and browser.member is None else ""
    pagination = paginate(_list_pages(entries, document, status), 1, data.page)
    buttons: list[list[InlineKeyboardButton]] = []
    if pagination.items:
        items = pagination.items[0]
        document += _preview_list(items)
        item_buttons = [
            InlineKeyboardButton(
                text=str(number),
                callback_data=data.model_copy(
                    update={
                        "page": pagination.page,
                        "target": entry.fact_id if isinstance(entry, MemoryFact) else str(entry.message_id),
                    }
                ).pack(),
            )
            for number, entry in enumerate(items, start=1)
        ]
        buttons.extend(
            item_buttons[offset : offset + _BUTTONS_PER_ROW] for offset in range(0, len(item_buttons), _BUTTONS_PER_ROW)
        )
        if pagination.total_pages > 1:
            buttons.append(build_pagination_row(pagination, lambda page: data.model_copy(update={"page": page}).pack()))
    elif data.view == "sources":
        document += Paragraph(_("No source messages saved in this chat."))
    elif browser.search:
        document += Paragraph(_("No memories match your search."))
    elif data.view == "history":
        document += Paragraph(_("No previous memory versions in this chat."))
    elif browser.member is not None:
        document += Paragraph(_("No saved memories for this user in this chat."))
    else:
        document += Paragraph(_("No saved memories in this chat."))
    if status:
        document += Paragraph(status)
    if footer := build_pagination_footer(pagination.page, pagination.total_pages):
        document += footer
    tabs: dict[MemoryListView, str] = {"show": _("Memories"), "history": _("History"), "sources": _("Sources")}
    buttons.append(
        [
            InlineKeyboardButton(text=label, callback_data=AIMemoryPageCallback(token=data.token, view=view).pack())
            for view, label in tabs.items()
            if view != data.view and browser.member is None
        ]
        + [
            InlineKeyboardButton(
                text=_("Help"),
                callback_data=data.model_copy(update={"page": pagination.page, "target": "help"}).pack(),
            )
        ]
    )
    if browser.member is None:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=_("Privacy"),
                    callback_data=data.model_copy(update={"page": pagination.page, "target": "privacy"}).pack(),
                )
            ]
        )
    return document, InlineKeyboardMarkup(inline_keyboard=buttons)


def _timestamp(time: datetime) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC")


def _fact_details(fact: MemoryFact, available_ids: set[int] | None) -> tuple[Doc, list[list[InlineKeyboardButton]]]:
    document = Doc(
        Heading(_("Memory details")),
        RichBlockQuote(*fact.text.splitlines()),
        Paragraph(KeyValue(_("ID"), Code(fact.fact_id))),
        Paragraph(KeyValue(_("Speaker"), Code(str(fact.speaker_tid)))),
        Paragraph(KeyValue(_("Time"), _timestamp(fact.occurred_at))),
    )
    if fact.editor_tid is not None:
        document += Paragraph(KeyValue(_("Edited by"), Code(str(fact.editor_tid))))
    if available_ids is not None:
        if fact.previous_id:
            document += Paragraph(KeyValue(_("Previous version"), Code(fact.previous_id)))
        available = ", ".join(str(identity) for identity in fact.evidence_ids if identity in available_ids)
        unavailable = ", ".join(str(identity) for identity in fact.evidence_ids if identity not in available_ids)
        if available:
            document += Paragraph(KeyValue(_("Source messages"), Code(available)))
        if unavailable:
            document += Paragraph(KeyValue(_("Source messages expired or unavailable"), Code(unavailable)))
    return document, [[InlineKeyboardButton(text=_("Copy fact ID"), copy_text=CopyTextButton(text=fact.fact_id))]]


def _source_details(data: AIMemoryPageCallback, source: MemorySource) -> tuple[Doc, list[list[InlineKeyboardButton]]]:
    part_length = TELEGRAM_MESSAGE_LENGTH_LIMIT
    parts = [source.text[offset : offset + part_length] for offset in range(0, len(source.text), part_length)]
    page = paginate(parts, 1, data.part)
    document = Doc(
        Heading(_("Source message")),
        Paragraph(KeyValue(_("ID"), Code(str(source.message_id)))),
        Paragraph(KeyValue(_("Time"), _timestamp(source.sent_at))),
        *(RichBlockQuote(*part.splitlines()) for part in page.items),
    )
    buttons = build_pagination_row(page, lambda part: data.model_copy(update={"part": part}).pack())
    if footer := build_pagination_footer(page.page, page.total_pages):
        document += footer
    return document, [buttons] if buttons else []


def memory_view(
    state: MemoryState,
    browser: MemoryBrowser,
    data: AIMemoryPageCallback,
) -> tuple[Doc, InlineKeyboardMarkup]:
    buttons: list[list[InlineKeyboardButton]] = []
    user_tid = browser.owner_tid
    excluded = user_tid in state.excluded_users
    if data.view == "sources":
        sources = [source for source in reversed(state.sources) if source.speaker_tid == user_tid]
        if not data.target:
            return _memory_list(data, sources, browser, excluded)
        source = next((entry for entry in sources if str(entry.message_id) == data.target), None)
        if source is None:
            document = Doc(_("This source message expired or is unavailable."))
        else:
            document, buttons = _source_details(data, source)
    else:
        superseded = {fact.previous_id for fact in state.facts}
        query = browser.search.casefold()
        target_tid = browser.member.tid if browser.member is not None else user_tid
        facts = [
            fact
            for fact in reversed(state.facts)
            if (fact.speaker_tid == target_tid or target_tid in fact.subject_tids)
            and (fact.fact_id in superseded) == (data.view == "history")
            and query in " ".join(fact.text.split()).casefold()
        ]
        facts.sort(key=lambda fact: (fact.occurred_at, fact.recorded_at), reverse=True)
        if not data.target:
            return _memory_list(data, facts, browser, excluded)
        fact = next((entry for entry in facts if entry.fact_id == data.target), None)
        if fact is None:
            document = Doc(_("This memory is no longer in this list."))
        else:
            document, buttons = _fact_details(
                fact,
                {source.message_id for source in state.sources} if browser.member is None else None,
            )
    return document, memory_back_markup(data, buttons)


def memory_back_markup(
    data: AIMemoryPageCallback, rows: Sequence[list[InlineKeyboardButton]] = ()
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *rows,
            [
                InlineKeyboardButton(
                    text=_("Back to list"),
                    callback_data=data.model_copy(update={"target": "", "part": 0}).pack(),
                )
            ],
        ]
    )
