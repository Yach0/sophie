from typing import Optional, cast

from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ArgCustomError
from babel.support import LazyProxy

from sophie_bot.db.models.notes_buttons import ButtonStyle
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.SophieButtonABC import AssButtonData, SophieButtonABC
from sophie_bot.utils.i18n import lazy_gettext as l_, gettext as _


class URLButton(SophieButtonABC):
    button_type_names = ("url",)
    ignored_entities = ("url",)

    allowed_protocols = ("http", "https")
    allowed_styles: tuple[ButtonStyle, ...] = ("primary", "danger", "success")

    def needed_type(self) -> tuple[LazyProxy, LazyProxy]:
        return l_("URL Button"), l_("URL Buttons")

    def examples(self) -> Optional[dict[str, Optional[LazyProxy]]]:
        return {
            "[Button name](btnurl:https://google.com)": None,
            "[Button name](buttonurl#success://example.com)": None,
        }

    async def parse(self, text: str, offset: int, entities: ArgEntities) -> tuple[int, AssButtonData[str]]:
        length, data = await super().parse(text, offset, entities)
        raw_url: str = data.arguments[0]
        raw_button_type = data.button_type
        style: ButtonStyle | None = None

        if "#" in raw_button_type:
            button_type, raw_style = raw_button_type.split("#", maxsplit=1)
            if raw_style not in self.allowed_styles:
                raise ArgCustomError(
                    _("Button style must be one of: primary, danger, success"),
                    offset=offset + self.data_offset,
                    length=len(raw_button_type),
                )

            style = cast(ButtonStyle, raw_style)
        else:
            button_type = raw_button_type

        if raw_url.startswith("//"):
            raw_url = f"https:{raw_url}"

        # Check protocols
        if not any(raw_url.startswith(protocol) for protocol in self.allowed_protocols):
            raise ArgCustomError(
                _("URL must start with http or https"),
                offset=offset + self.data_offset,
                length=len(raw_url),
            )

        return length, AssButtonData(
            button_type=button_type,
            title=data.title,
            arguments=(raw_url,),
            same_row=data.same_row,
            style=style,
        )
