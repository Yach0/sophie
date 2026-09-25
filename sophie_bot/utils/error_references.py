from stfu_tg import Code, KeyValue
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import gettext as _


def error_reference_elements(sentry_event_id: str | None, logfire_trace_id: str | None) -> tuple[str | Element, ...]:
    if sentry_event_id and logfire_trace_id:
        return (
            " ",
            KeyValue(_("Reference ID"), Code(sentry_event_id)),
            " ",
            KeyValue(_("Trace ID"), Code(logfire_trace_id)),
        )
    if sentry_event_id:
        return (" ", KeyValue(_("Reference ID"), Code(sentry_event_id)))
    if logfire_trace_id:
        return (" ", KeyValue(_("Trace ID"), Code(logfire_trace_id)))
    return ()
