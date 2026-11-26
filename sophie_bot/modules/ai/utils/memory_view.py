from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aiogram.types import InlineKeyboardMarkup
from pydantic import BaseModel
from stfu_tg import (
    Button,
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
from sophie_bot.modules.ai.utils.memory_edits import MemoryEditPreview
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.pagination import build_pagination_footer, build_pagination_row, paginate

_PAGE_SIZE = 16
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


def memory_edit_confirmation(preview: MemoryEditPreview) -> Doc:
    proposal = preview.proposal
    title = {
        "add": _("Save this memory?"),
        "update": _("Update this memory?"),
        "delete": _("Delete these memories and their history?"),
    }[proposal.action]
    doc = Doc(Heading(title))
    if proposal.action == "add":
        subject = proposal.subject_tid or proposal.source.speaker_tid
        doc += Paragraph(KeyValue(_("Subject's Telegram user ID"), Code(str(subject))))
        doc += Paragraph(KeyValue(_("Topic"), proposal.event))
    for fact in preview.targets:
        doc += Paragraph(Code(fact.fact_id), " ", _preview(fact.text, 180))
        doc += Paragraph(KeyValue(_("Speaker's Telegram user ID"), Code(str(fact.speaker_tid))))
    if proposal.text:
        doc += RichBlockQuote(*proposal.text.splitlines())
    return doc


def _preview_item(entry: MemoryFact | MemorySource, data: AIMemoryPageCallback) -> ListItem:
    target = entry.fact_id if isinstance(entry, MemoryFact) else str(entry.message_id)
    details = data.model_copy(update={"target": target})
    actions = []
    if data.view == "show":
        actions.extend((" ", Button(_("Edit"), callback_data=details.model_copy(update={"edit": True}).pack())))
    actions.extend((" ", Button(_("Details"), callback_data=details.pack())))
    return ListItem(Paragraph(_preview(entry.text, _PREVIEW_LENGTH), *actions))


def _list_header(data: AIMemoryPageCallback, browser: MemoryBrowser) -> Doc:
    if browser.member is not None:
        return Doc(Heading(Template(_("AI memory for {user}"), user=_preview(browser.member.name, 120))))
    title = {"show": _("Your AI memory"), "history": _("Memory history"), "sources": _("Your source messages")}
    header = Doc(Heading(title[data.view]))
    if browser.search and data.view != "sources":
        header += Paragraph(Template(_("Search: {text}"), text=_preview(browser.search, 120)))
    return header


def _list_pages(items: Sequence[ListItem], header: Doc, status: str) -> list[list[ListItem]]:
    pages: list[list[ListItem]] = []
    footer = build_pagination_footer(len(items) - 1, len(items))
    overhead = len(Doc(header, OrderedList(), Paragraph(status) if status else "", footer).to_rich().encode("utf-8"))
    page_size = overhead
    page: list[ListItem] = []
    for item in items:
        size = len(item.to_rich().encode("utf-8"))
        if page and (len(page) == _PAGE_SIZE or page_size + size > _MAX_RICH_PAGE_BYTES):
            pages.append(page)
            page_size = overhead
            page = []
        page.append(item)
        page_size += size
    if page:
        pages.append(page)
    return pages


def _memory_list(
    data: AIMemoryPageCallback, entries: Sequence[MemoryFact | MemorySource], browser: MemoryBrowser, excluded: bool
) -> tuple[Doc, InlineKeyboardMarkup | None]:
    document = _list_header(data, browser)
    status = _("Memory capture is off for you.") if excluded and browser.member is None else ""
    first_page = data.model_copy(update={"page": 0})
    items = [_preview_item(entry, first_page) for entry in entries]
    pagination = paginate(_list_pages(items, document, status), 1, data.page)
    if pagination.items:
        document += OrderedList(*pagination.items[0])
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
    buttons = build_pagination_row(pagination, lambda page: first_page.model_copy(update={"page": page}).pack())
    return document, InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


def _timestamp(time: datetime) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC")


def _fact_details(fact: MemoryFact, available_ids: set[int] | None) -> Doc:
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
    return document


def _source_details(data: AIMemoryPageCallback, source: MemorySource) -> tuple[Doc, InlineKeyboardMarkup | None]:
    part_length = TELEGRAM_MESSAGE_LENGTH_LIMIT
    parts = [source.text[offset : offset + part_length] for offset in range(0, len(source.text), part_length)]
    page = paginate(parts, 1, data.part or 0)
    document = Doc(
        Heading(_("Source message")),
        Paragraph(KeyValue(_("ID"), Code(str(source.message_id)))),
        Paragraph(KeyValue(_("Time"), _timestamp(source.sent_at))),
        *(RichBlockQuote(*part.splitlines()) for part in page.items),
    )
    buttons = build_pagination_row(page, lambda part: data.model_copy(update={"part": part}).pack())
    if footer := build_pagination_footer(page.page, page.total_pages):
        document += footer
    return document, InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


def memory_facts(state: MemoryState, browser: MemoryBrowser, view: MemoryListView) -> list[MemoryFact]:
    superseded = {fact.previous_id for fact in state.facts}
    query = browser.search.casefold()
    target_tid = browser.member.tid if browser.member is not None else browser.owner_tid
    facts = [
        fact
        for fact in reversed(state.facts)
        if (fact.speaker_tid == target_tid or target_tid in fact.subject_tids)
        and (fact.fact_id in superseded) == (view == "history")
        and query in " ".join(fact.text.split()).casefold()
    ]
    return sorted(facts, key=lambda fact: (fact.occurred_at, fact.recorded_at), reverse=True)


def memory_view(
    state: MemoryState,
    browser: MemoryBrowser,
    data: AIMemoryPageCallback,
) -> tuple[Doc, InlineKeyboardMarkup | None]:
    user_tid = browser.owner_tid
    excluded = user_tid in state.excluded_users
    if data.view == "sources":
        sources = [source for source in reversed(state.sources) if source.speaker_tid == user_tid]
        if not data.target:
            return _memory_list(data, sources, browser, excluded)
        source = next((entry for entry in sources if str(entry.message_id) == data.target), None)
        if source is None:
            return Doc(_("This source message expired or is unavailable.")), None
        return _source_details(data, source)
    facts = memory_facts(state, browser, data.view)
    if not data.target:
        return _memory_list(data, facts, browser, excluded)
    fact = next((entry for entry in facts if entry.fact_id == data.target), None)
    if fact is None:
        return Doc(_("This memory is no longer in this list.")), None
    available_ids = {source.message_id for source in state.sources} if browser.member is None else None
    return _fact_details(fact, available_ids), None
