from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from aiogram.types import InlineKeyboardButton
from stfu_tg import Footer, Template

from sophie_bot.utils.i18n import gettext as _


class _VisibleTextLength(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.length = 0

    def handle_data(self, data: str) -> None:
        self.length += len(data.encode("utf-16-le")) // 2


def telegram_html_length(html: str) -> int:
    counter = _VisibleTextLength()
    counter.feed(html)
    counter.close()
    return counter.length


@dataclass(frozen=True, slots=True)
class PaginationPage[ItemT]:
    page: int
    total_pages: int
    items: tuple[ItemT, ...]
    has_previous: bool
    has_next: bool


def paginate[ItemT](items: Sequence[ItemT], page_size: int, requested_page: int = 0) -> PaginationPage[ItemT]:
    if page_size <= 0:
        raise ValueError("page_size must be positive")

    total_pages = (len(items) + page_size - 1) // page_size
    if total_pages == 0:
        return PaginationPage(0, 0, (), False, False)

    page = min(max(requested_page, 0), total_pages - 1)
    start = page * page_size
    return PaginationPage(
        page=page,
        total_pages=total_pages,
        items=tuple(items[start : start + page_size]),
        has_previous=page > 0,
        has_next=page < total_pages - 1,
    )


def build_pagination_footer(page_index: int, total_pages: int) -> Footer | None:
    if total_pages <= 1:
        return None
    return Footer(Template(_("Page {page} of {pages}"), page=page_index + 1, pages=total_pages))


def build_pagination_row(
    page: PaginationPage[Any], callback_factory: Callable[[int], str]
) -> list[InlineKeyboardButton]:
    buttons: list[InlineKeyboardButton] = []
    if page.has_previous:
        buttons.append(
            InlineKeyboardButton(
                text=_("◀️ Previous"),
                callback_data=callback_factory(page.page - 1),
            )
        )
    if page.has_next:
        buttons.append(
            InlineKeyboardButton(
                text=_("Next ▶️"),
                callback_data=callback_factory(page.page + 1),
            )
        )
    return buttons
