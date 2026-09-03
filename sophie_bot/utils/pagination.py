from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton
from beanie import PydanticObjectId

from sophie_bot.utils.i18n import gettext as _


@dataclass(frozen=True, slots=True)
class PaginationPage[ItemT]:
    """An immutable, clamped page of a sequence."""

    page: int
    total_pages: int
    items: tuple[ItemT, ...]
    has_previous: bool
    has_next: bool

    @property
    def index(self) -> int:
        """Return the zero-based clamped page index."""
        return self.page

    @property
    def previous_page(self) -> int | None:
        return self.page - 1 if self.has_previous else None

    @property
    def next_page(self) -> int | None:
        return self.page + 1 if self.has_next else None


class Paginator[ItemT]:
    """Paginate a sequence without knowing anything about its consumer."""

    def __init__(self, items: Sequence[ItemT], page_size: int) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self._items = items
        self._page_size = page_size

    def page(self, requested_page: int = 0) -> PaginationPage[ItemT]:
        requested_page = max(requested_page, 0)
        total_pages = (len(self._items) + self._page_size - 1) // self._page_size
        if total_pages == 0:
            return PaginationPage(0, 0, (), False, False)

        page = min(requested_page, total_pages - 1)
        start = page * self._page_size
        end = start + self._page_size
        return PaginationPage(
            page=page,
            total_pages=total_pages,
            items=tuple(self._items[start:end]),
            has_previous=page > 0,
            has_next=page < total_pages - 1,
        )


def paginate[ItemT](items: Sequence[ItemT], page_size: int, requested_page: int = 0) -> PaginationPage[ItemT]:
    return Paginator(items, page_size).page(requested_page)


class PaginationCallback(CallbackData, prefix="page"):
    """Callback payload shared by paginated list screens."""

    scope: str
    page: int


def build_pagination_row[CallbackT](
    page: PaginationPage[Any], callback_factory: Callable[[int], CallbackT]
) -> list[InlineKeyboardButton]:
    """Build a compact previous/next row for a page."""
    buttons: list[InlineKeyboardButton] = []
    if page.has_previous:
        buttons.append(
            InlineKeyboardButton(
                text=_("◀️ Previous"),
                callback_data=_pack_callback(callback_factory(page.page - 1)),
            )
        )
    if page.has_next:
        buttons.append(
            InlineKeyboardButton(
                text=_("Next ▶️"),
                callback_data=_pack_callback(callback_factory(page.page + 1)),
            )
        )
    return buttons


def pagination_row[CallbackT](
    page: PaginationPage[Any], callback_factory: Callable[[int], CallbackT]
) -> list[InlineKeyboardButton]:
    """Backward-compatible concise alias for :func:`build_pagination_row`."""
    return build_pagination_row(page, callback_factory)


def _pack_callback[CallbackT](callback: CallbackT) -> str:
    if isinstance(callback, str):
        return callback
    pack = getattr(callback, "pack", None)
    if not callable(pack):
        raise TypeError("callback_factory must return a packed callback or CallbackData")
    return pack()


class PaginationContext:
    """FSM-backed storage for serializable list query parameters."""

    _SCOPE_KEY = "pagination_scope"
    _PARAMS_KEY = "pagination_params"

    def __init__(self, state: FSMContext) -> None:
        self._state = state

    async def start(self, scope: str, params: Mapping[str, Any] | None = None) -> None:
        await self._state.update_data(
            {
                self._SCOPE_KEY: scope,
                self._PARAMS_KEY: _json_safe(dict(params or {})),
            }
        )

    async def get(self, scope: str) -> dict[str, Any] | None:
        data = await self._state.get_data()
        if data.get(self._SCOPE_KEY) != scope:
            return None
        params = data.get(self._PARAMS_KEY)
        return dict(params) if isinstance(params, dict) else None

    async def clear(self, scope: str | None = None) -> None:
        data = await self._state.get_data()
        if scope is not None and data.get(self._SCOPE_KEY) != scope:
            return
        data.pop(self._SCOPE_KEY, None)
        data.pop(self._PARAMS_KEY, None)
        await self._state.set_data(data)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, PydanticObjectId):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    raise TypeError(f"Pagination parameters must be JSON-safe, got {type(value).__name__}")


__all__ = [
    "PaginationCallback",
    "PaginationContext",
    "PaginationPage",
    "Paginator",
    "build_pagination_row",
    "paginate",
    "pagination_row",
]
