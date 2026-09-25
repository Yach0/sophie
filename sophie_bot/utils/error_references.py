from stfu_tg import Code, Doc, KeyValue
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import gettext as _


def error_reference_elements(sentry_event_id: str | None, logfire_trace_id: str | None) -> tuple[str | Element, ...]:
    if sentry_event_id and logfire_trace_id:
        return (
            " ",
            KeyValue(
                _("Reference IDs"),
                Doc(
                    KeyValue(_("Sentry"), Code(sentry_event_id)),
                    KeyValue(_("Logfire"), Code(logfire_trace_id)),
                ),
            ),
        )
    reference_id = sentry_event_id or logfire_trace_id
    if reference_id:
        return (" ", KeyValue(_("Reference ID"), Code(reference_id)))
    return ()
