from stfu_tg.doc import Element


class SophieException(Exception):
    """Base class for all exceptions"""

    def __init__(self, *docs: str | Element):
        self.docs = docs
        # Set when the raiser already reported the failure to Sentry with its own context attached.
        # The top-level error handler reuses it instead of capturing a second, context-free event
        # that would give the user a reference ID matching neither issue.
        self.sentry_event_id: str | None = None
