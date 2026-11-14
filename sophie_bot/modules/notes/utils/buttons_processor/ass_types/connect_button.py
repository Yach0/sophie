from babel.support import LazyProxy

from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_button_abc import SophieButtonABC
from sophie_bot.utils.i18n import lazy_gettext as l_


class ConnectButton(SophieButtonABC):
    button_type_names = ("connect",)

    @staticmethod
    def needed_type() -> tuple[LazyProxy, LazyProxy]:
        return l_("Connect Button"), l_("Connect Buttons")

    @property
    def examples(self) -> dict[str, LazyProxy | None] | None:
        return {
            "[Button name](btnconnect)": None,
        }
