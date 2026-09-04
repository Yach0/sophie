from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton

from sophie_bot.utils.i18n import gettext as _


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
