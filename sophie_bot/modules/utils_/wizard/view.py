from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import gettext as _


@dataclass(frozen=True, slots=True)
class WizardView:
    doc: Element
    markup: InlineKeyboardMarkup | None = None


def build_wizard_navigation(
    *,
    done_callback: str | None = None,
    back_callback: str | None = None,
    cancel_callback: str | None = None,
    pagination: list[InlineKeyboardButton] | None = None,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if pagination:
        rows.append(pagination)
    if done_callback is not None:
        rows.append([InlineKeyboardButton(text=_("✅ Done"), callback_data=done_callback)])

    bottom_row: list[InlineKeyboardButton] = []
    if back_callback is not None:
        bottom_row.append(InlineKeyboardButton(text=_("⬅️ Back"), callback_data=back_callback))
    if cancel_callback is not None:
        bottom_row.append(InlineKeyboardButton(text=_("❌ Cancel"), callback_data=cancel_callback))
    if bottom_row:
        rows.append(bottom_row)

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
