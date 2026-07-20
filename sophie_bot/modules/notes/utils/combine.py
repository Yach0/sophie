from stfu_tg import Doc, PreformattedHTML
from stfu_tg.doc import Element

from sophie_bot.db.models.notes import Saveable, SaveableParseMode


def combine_saveables(*items: tuple[Saveable, Element]) -> Saveable:
    text = Doc()
    text += items[0][1]
    text += PreformattedHTML(items[0][0].text or "")

    for saveable, title in items[1:]:
        text += " "
        text += title
        text += PreformattedHTML(saveable.text or "")

    return Saveable(
        text=text.to_html(),
        file=items[0][0].file,
        files=items[0][0].files,
        parse_mode=SaveableParseMode.html,
    )
