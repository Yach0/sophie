from ass_tg.types.logic import OptionalArg
from ass_tg.types.reverse import ReverseArg
from ass_tg.types.text import TextArg
from babel.support import LazyProxy

from sophie_bot.modules.notes.utils.buttons_processor.ass_types.parse_arg import ButtonsArg
from sophie_bot.utils.i18n import lazy_gettext as l_


class TextWithButtonsArg(ReverseArg):
    def __init__(self, description: str | LazyProxy | None = None):
        super().__init__(
            description,
            text=OptionalArg(TextArg(l_("Content"), parse_entities=True)),
            # `ButtonsArg` already safely parses to `[]` when no buttons are present,
            # and it needs to be able to provide `get_start()` for `ReverseArg`.
            buttons=ButtonsArg(l_("Buttons")),
        )
