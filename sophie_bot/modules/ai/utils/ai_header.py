from typing import Final, Literal, cast

from stfu_tg import Doc, HList, PreformattedHTML, RichTable, RichTableCell
from stfu_tg.doc import Element

from sophie_bot.constants import AI_EMOJI
from sophie_bot.utils.feature_flags import FeatureType, get_value

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
AI_SIMPLE_HEADER_PREFIX: Final[str] = f"{AI_EMOJI} 🔋"

AIHeaderStyle = Literal["table", "disable", "simple"]
AIHeaderPurpose = Literal["chatbot", "filters", "proactive_replies", "translation", "summary"]

_HEADER_STYLE_FLAG_BY_PURPOSE: Final[dict[AIHeaderPurpose, FeatureType]] = {
    "chatbot": "ai_chatbot_header_style",
    "filters": "ai_filters_header_style",
    "proactive_replies": "ai_proactive_replies_header_style",
    "translation": "ai_translations_header_style",
    "summary": "ai_chat_summaries_header_style",
}


async def get_ai_header_style(purpose: AIHeaderPurpose, chat_tid: int) -> AIHeaderStyle:
    return cast(AIHeaderStyle, await get_value(_HEADER_STYLE_FLAG_BY_PURPOSE[purpose], chat_tid=chat_tid))


def ai_table_header(status: Element | str = "", battery: Element | str = "") -> RichTable:
    """The one-row header every AI message carries: who is speaking, what it did, what is left."""
    return RichTable(
        [RichTableCell(AI_HEADER_LABEL), RichTableCell(status, align="center"), RichTableCell(battery, align="right")],
        bordered=True,
    )


def build_ai_header(
    style: AIHeaderStyle,
    status: Element | str = "",
    battery: Element | str = "",
) -> Element | str | None:
    if style == "disable":
        return None
    if style == "simple":
        return HList(battery or "🔋")
    return ai_table_header(status, battery)


def build_ai_message_doc(style: AIHeaderStyle, header: Element | str | None, *body: Element | str | None) -> Doc:
    body_doc = Doc(*body)
    if header is None:
        return body_doc
    if style == "simple":
        return Doc(HList(AI_EMOJI, body_doc, divider=" "), "\n", header)
    return Doc(header, body_doc)


def ai_credit_header(percentage: int) -> Element:
    return HList(_battery_custom_emoji(percentage), str(percentage) + "%", divider=" ")
