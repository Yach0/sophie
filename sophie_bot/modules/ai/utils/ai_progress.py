from __future__ import annotations

from random import choice
from typing import Final

from stfu_tg import HList, PreformattedHTML
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import gettext as _

AI_PROGRESS_CUSTOM_EMOJI_IDS: Final[tuple[str, ...]] = (
    "5256211041615889001",
    "5258317508326214175",
    "5258417125797678733",
    "5258494813166129825",
    "5258333275151157392",
    "5258331634473650007",
)


def random_ai_progress_custom_emoji_id() -> str:
    return choice(AI_PROGRESS_CUSTOM_EMOJI_IDS)


def ai_progress_custom_emoji(emoji_id: str | None = None, fallback: str = "💭") -> Element:
    progress_emoji_id = emoji_id or random_ai_progress_custom_emoji_id()
    return PreformattedHTML(f'<tg-emoji emoji-id="{progress_emoji_id}">{fallback}</tg-emoji>')


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
            _("Sorting through the context..."),
            _("Connecting the dots..."),
            _("Reviewing the conversation..."),
            _("Drafting a reply..."),
            _("Checking what matters..."),
            _("Putting it together..."),
            _("Thinking this through..."),
            _("Finding the right angle..."),
            _("Weighing the options..."),
            _("Tracing the details..."),
            _("Building the answer..."),
            _("Making sense of it..."),
        )
    )


def ai_progress_line(text: Element | str, emoji_id: str | None = None, suffix: Element | str | None = None) -> Element:
    """The in-progress placeholder for every AI feature.

    Deliberately not the AI table header: while generation runs there is no generated title to show
    and no final quota reading to report, so the placeholder stays a plain line.
    """
    return HList(ai_progress_custom_emoji(emoji_id), text, suffix, divider=" ")
