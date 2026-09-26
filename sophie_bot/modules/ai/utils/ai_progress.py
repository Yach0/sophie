from __future__ import annotations

from random import choice

from sophie_bot.utils.i18n import gettext as _


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
