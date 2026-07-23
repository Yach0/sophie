from typing import Final

from stfu_tg import HList, PreformattedHTML, RichTable, RichTableCell
from stfu_tg.doc import Element

from sophie_bot.constants import AI_EMOJI

_LOW_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5819177212833697095"
_MIDDLE_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5818860416045945285"
_HIGH_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5816915599019741395"


def _get_battery_custom_emoji_id(percentage: int) -> str:
    if percentage >= 66:
        return _HIGH_BATTERY_CUSTOM_EMOJI_ID
    if percentage >= 33:
        return _MIDDLE_BATTERY_CUSTOM_EMOJI_ID
    return _LOW_BATTERY_CUSTOM_EMOJI_ID


def _battery_custom_emoji(percentage: int) -> Element:
    emoji_id = _get_battery_custom_emoji_id(percentage)
    return PreformattedHTML(f'<tg-emoji emoji-id="{emoji_id}">🔋</tg-emoji>')


# First cell of every AI message. Replies are detected by it, so it must stay exactly this: the
# rich renderer shows the cells as a table, and to_html() joins them with the same separator.
AI_HEADER_LABEL: Final[str] = f"{AI_EMOJI} AI"
AI_HEADER_SEPARATOR: Final[str] = " | "


def ai_table_header(status: Element | str = "", battery: Element | str = "") -> RichTable:
    """The one-row header every AI message carries: who is speaking, what it did, what is left."""
    return RichTable(
        [RichTableCell(AI_HEADER_LABEL), RichTableCell(status, align="center"), RichTableCell(battery, align="right")],
        bordered=True,
    )


def ai_credit_header(percentage: int) -> Element:
    return HList(_battery_custom_emoji(percentage), str(percentage) + "%", divider=" ")
