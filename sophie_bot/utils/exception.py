from __future__ import annotations

from typing import TYPE_CHECKING

from stfu_tg.doc import Element

if TYPE_CHECKING:
    from sophie_bot.utils.i18n import LazyProxy


class SophieException(Exception):
    """Base class for all exceptions"""

    def __init__(self, *docs: str | Element | LazyProxy):
        self.docs = docs
        # Set when the raiser already reported the failure to Sentry with its own context attached.
        # The top-level error handler reuses it instead of capturing a second, context-free event
        # that would give the user a reference ID matching neither issue.
        self.sentry_event_id: str | None = None
