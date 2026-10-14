from babel.support import LazyProxy

from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_button_abc import SophieButtonABC
from sophie_bot.utils.i18n import lazy_gettext as l_


class CaptchaButton(SophieButtonABC):
    button_type_names = ("welcomesecurity",)

    @staticmethod
    def needed_type() -> tuple[LazyProxy, LazyProxy]:
        return l_("Captcha Button"), l_("Captcha Buttons")

    @staticmethod
    def examples() -> dict[str, LazyProxy | None] | None:
        return {
            "[Button name](btnwelcomesecurity)": None,
        }
