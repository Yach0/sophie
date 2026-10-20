from babel.support import LazyProxy

from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_button_abc import SophieButtonABC
from sophie_bot.utils.i18n import lazy_gettext as l_


class DeleteButton(SophieButtonABC):
    button_type_names = ("delmsg",)
    allowed_prefixes = ("btn", "button", "")

    @staticmethod
    def needed_type() -> tuple[LazyProxy, LazyProxy]:
        return l_("Delete Button"), l_("Delete Buttons")

    @property
    def examples(self) -> dict[str, LazyProxy | None] | None:
        return {
            "[Button name](delmsg)": None,
        }
