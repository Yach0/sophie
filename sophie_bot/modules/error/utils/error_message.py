import random
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from stfu_tg import BlockQuote, Doc, Italic, Title
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.modules.error.utils.haikus import HAIKUS
from sophie_bot.utils.error_references import error_reference_elements
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def get_error_message(exception: Exception) -> tuple[str | Element | LazyProxy, ...]:
    if isinstance(exception, SophieException):
        # It has 'docs' field
        return exception.docs

    # Return either as itself if the type is based on Core (STFU-able) or stringify as italic
    return tuple(x if isinstance(x, Element) else Italic(str(x)) for x in exception.args)


_DEFAULT_ERROR_TITLE = l_("😞 I've got an error trying to process this update")


def generic_error_message(
    exception: Exception,
    sentry_event_id: str | None,
    logfire_trace_id: str | None = None,
    hide_contact: bool = False,
    title: str | LazyProxy | Element = _DEFAULT_ERROR_TITLE,
) -> dict[str, Any]:
    return {
        "text": str(
            Doc(
                Title(title),
                *get_error_message(exception),
                *(
                    ()
                    if isinstance(exception, SophieException)
                    else (
                        " ",
                        BlockQuote(Doc(*random.choice(HAIKUS))),
                    )
                ),
                *error_reference_elements(sentry_event_id, logfire_trace_id),
            )
        ),
        "reply_markup": InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=_("💬 Contact Sophie support"), url=CONFIG.support_link)]]
        )
        if not hide_contact
        else None,
    }
