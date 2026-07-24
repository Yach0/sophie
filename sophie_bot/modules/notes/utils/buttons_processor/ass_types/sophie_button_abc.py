from __future__ import annotations

from abc import ABC
from dataclasses import dataclass

from ass_tg.entities import ArgEntities

from sophie_bot.db.models.notes_buttons import ButtonStyle
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.markdown_link_argument import MarkdownLinkArgument


@dataclass
class AssButtonData[A]:
    button_type: str
    title: str
    arguments: tuple[A]
    same_row: bool = False
    style: ButtonStyle | None = None


class SophieButtonABC(MarkdownLinkArgument, ABC):
    button_type_names: tuple[str, ...]  # Must be defined in subclasses
    separator = ":"
    allowed_prefixes: tuple[str, ...] = (
        "button",
        "btn",
    )
    same_row_suffixes = ("same", "^")

    _used_button_type: str

    @property
    def used_prefix(self) -> str | None:
        return next((prefix for prefix in self.allowed_prefixes if self._link_data.startswith(prefix)), None)

    @property
    def data_offset(self) -> int:
        return super().data_offset + len(self.used_prefix or "") + len(self._used_button_type)

    def check(self, text: str, entities: ArgEntities) -> bool:
        if not super().check(text, entities):
            return False

        if self.used_prefix is None:
            return False

        link_data = self._link_data.removeprefix(self.used_prefix)

        for name in self.button_type_names:
            if link_data == name:
                return True
            next_char_idx = len(name)
            if (
                next_char_idx < len(link_data)
                and link_data[next_char_idx] in (self.separator, "#")
                and link_data[:next_char_idx] == name
            ):
                return True
        return False

    async def parse(self, text: str, offset: int, entities: ArgEntities) -> tuple[int, AssButtonData[str]]:  # ty: ignore[invalid-method-override]
        length, (_link_name, link_data) = await super().parse(text, offset, entities)
        prefix = self.used_prefix
        if prefix:
            link_data = link_data.removeprefix(prefix)

        if self.separator in link_data:
            button_type, args_text = link_data.split(self.separator, maxsplit=1)
        else:
            button_type = link_data
            args_text = ""
        self._used_button_type = button_type

        # Parse same row
        same_row = False
        if args_text in self.same_row_suffixes:
            args_text = ""
            same_row = True
        elif self.separator in args_text:
            args_text_split = args_text.rsplit(self.separator, maxsplit=1)
            if args_text_split[-1] in self.same_row_suffixes:
                args_text = args_text_split[0]
                same_row = True
        elif button_type in self.same_row_suffixes:  # Should not happen usually as prefix is stripped
            pass

        return length, AssButtonData(
            button_type=button_type, title=self._link_name, arguments=(args_text,), same_row=same_row
        )
