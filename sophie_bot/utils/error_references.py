from stfu_tg import Code, KeyValue
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import gettext as _


def error_reference_elements(sentry_event_id: str | None) -> tuple[str | Element, ...]:
    if sentry_event_id:
        return (" ", KeyValue(_("Reference ID"), Code(sentry_event_id)))
    return ()
