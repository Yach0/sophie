from __future__ import annotations

from stfu_tg import Code, HList
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils.ai_header import battery_custom_emoji


def format_credit_amount(amount: int) -> Element:
    return HList(battery_custom_emoji(), Code(f"{amount:,}"), divider=" ")
