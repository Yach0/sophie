from collections.abc import Sequence
from typing import Final, Literal

from redis.asyncio import Redis
from stfu_tg import CustomEmoji, Doc, HList, LineBreak
from stfu_tg.doc import Element

from sophie_bot.constants import AI_EMOJI
from sophie_bot.utils.feature_flags import FeatureType, get_value

AI_CUSTOM_EMOJI_ID: Final[str] = "5325547803936572038"
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
    return CustomEmoji(_get_battery_custom_emoji_id(percentage), "🔋")


AIHeaderStyle = Literal["disable", "simple"]
AIHeaderPurpose = Literal["chatbot", "filters", "proactive_replies", "translation", "summary"]

_HEADER_STYLE_FLAG_BY_PURPOSE: Final[dict[AIHeaderPurpose, FeatureType]] = {
    "chatbot": "ai_chatbot_header_style",
    "filters": "ai_filters_header_style",
    "proactive_replies": "ai_proactive_replies_header_style",
    "translation": "ai_translations_header_style",
    "summary": "ai_chat_summaries_header_style",
}


async def get_ai_header_style(purpose: AIHeaderPurpose, chat_tid: int, *, redis: Redis) -> AIHeaderStyle:
    configured_style = await get_value(
        _HEADER_STYLE_FLAG_BY_PURPOSE[purpose],
        chat_tid=chat_tid,
        redis=redis,
    )
    return "disable" if configured_style == "disable" else "simple"


def build_ai_header(style: AIHeaderStyle, battery: Element | str = "") -> Element | str | None:
    if style == "disable":
        return None
    return HList(battery or "🔋")


def build_ai_message_doc(
    header: Element | str | None,
    *body: Element | str | None,
    tool_labels: Sequence[str] = (),
) -> Doc:
    if header is None:
        return Doc(*body)
    tools = f"({', '.join(tool_labels)})" if tool_labels else None
    return Doc(
        HList(
            HList(CustomEmoji(AI_CUSTOM_EMOJI_ID, AI_EMOJI), tools, *body, divider=" "),
            LineBreak(),
            header,
            divider="",
        )
    )


def ai_credit_header(percentage: int, model_label: str | None = None) -> Element:
    model = f"({model_label})" if model_label else None
    return HList(_battery_custom_emoji(percentage), str(percentage) + "%", model, divider=" ")
