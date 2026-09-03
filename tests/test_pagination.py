from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardButton

from sophie_bot.utils.pagination import build_pagination_row, paginate


def test_empty_input_has_no_pages_or_navigation() -> None:
    page = paginate([], 8)
    assert (page.page, page.total_pages, page.items, page.has_previous, page.has_next) == (0, 0, (), False, False)
    assert build_pagination_row(page, lambda page_number: f"page:{page_number}") == []


def test_first_middle_and_last_pages() -> None:
    first = paginate(list(range(17)), 8, 0)
    middle = paginate(list(range(17)), 8, 1)
    last = paginate(list(range(17)), 8, 2)

    assert first.items == tuple(range(8)) and first.has_next and not first.has_previous
    assert middle.items == tuple(range(8, 16)) and middle.has_previous and middle.has_next
    assert last.items == (16,) and last.has_previous and not last.has_next


def test_negative_and_oversized_pages_are_clamped() -> None:
    assert paginate([1, 2], 1, 99).page == 1
    assert paginate([1, 2], 1, -2).page == 0


def test_invalid_page_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        paginate([1], 0)
    with pytest.raises(ValueError):
        paginate([1], -1)


def test_navigation_row_uses_consumer_packed_callbacks() -> None:
    page = paginate(list(range(24)), 8, 1)
    buttons = build_pagination_row(page, lambda page_number: f"notes:{page_number}")
    assert [button.text for button in buttons] == ["◀️ Previous", "Next ▶️"]
    assert all(isinstance(button, InlineKeyboardButton) for button in buttons)
    assert [button.callback_data for button in buttons] == ["notes:0", "notes:2"]
