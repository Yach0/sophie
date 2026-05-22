from __future__ import annotations

from ass_tg.entities import ArgEntities

from sophie_bot.modules.notes.utils.buttons.models import ButtonLayout
from sophie_bot.modules.notes.utils.buttons.storage import buttons_from_ass
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.parse_arg import ButtonsArg


async def parse_buttons_from_text(text: str) -> ButtonLayout:
    entities = ArgEntities([])
    arg = ButtonsArg()
    arg.check(text, entities)
    _length, raw_buttons = await arg.parse(text, 0, entities)
    return buttons_from_ass(raw_buttons)
