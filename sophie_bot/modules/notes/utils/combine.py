from stfu_tg import Doc, PreformattedHTML
from stfu_tg.doc import Element

from sophie_bot.db.models.notes import Saveable, SaveableParseMode


def combine_saveables(*items: tuple[Saveable, Element]) -> Saveable:
    """This function combines multiple saveables into one."""

    text = Doc()
    for idx, (saveable, title) in enumerate(items):
        if idx != 0:
            text += " "
        text += title

        text += PreformattedHTML(saveable.text or "")

    return Saveable(
        text=text.to_html(),
        file=items[0][0].file,
        files=items[0][0].files,
        parse_mode=SaveableParseMode.html,
    )
