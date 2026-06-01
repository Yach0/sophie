from __future__ import annotations

from random import choice
from typing import Final

from stfu_tg import HList, PreformattedHTML
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import gettext as _

DEFAULT_AI_PROGRESS_CUSTOM_EMOJI_ID: Final[str] = "5258317508326214175"
AI_PROGRESS_CUSTOM_EMOJI_IDS: Final[tuple[str, ...]] = (
    "5256211041615889001",
    "5257997017866585370",
    DEFAULT_AI_PROGRESS_CUSTOM_EMOJI_ID,
    "5258417125797678733",
    "5258464881539040501",
    "5258494813166129825",
    "5258331634473650007",
)


def random_ai_progress_custom_emoji_id() -> str:
    return choice(AI_PROGRESS_CUSTOM_EMOJI_IDS)


def ai_progress_custom_emoji(emoji_id: str | None = None, fallback: str = "💭") -> Element:
    return PreformattedHTML(
        f'<tg-emoji emoji-id="{emoji_id or DEFAULT_AI_PROGRESS_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'
    )


def random_ai_thinking_text() -> str:
    return choice(
        (
            _("Thinking..."),
            _("Working on it..."),
            _("Let me think..."),
            _("Generating response..."),
            _("Preparing an answer..."),
            _("Reading the context..."),
            _("Checking the details..."),
            _("Looking into it..."),
        )
    )


def ai_progress_line(text: str, emoji_id: str | None = None, suffix: str | None = None) -> Element:
    return HList(ai_progress_custom_emoji(emoji_id), text, suffix, divider=" ")
