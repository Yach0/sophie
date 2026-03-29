from __future__ import annotations

from sophie_bot.constants import AI_CREDIT_EMOJI


def format_credit_amount(amount: int) -> str:
    return f"{AI_CREDIT_EMOJI} {amount:,}"
