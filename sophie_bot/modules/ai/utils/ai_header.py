from collections.abc import Sequence
from typing import Any, Final, Literal

from redis.asyncio import Redis
from stfu_tg import CustomEmoji, Doc, HList, Italic, Paragraph
from stfu_tg.doc import Element

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.utils.ai_tool import AITool
from sophie_bot.utils.feature_flags import FeatureType, get_value

AI_CUSTOM_EMOJI_ID: Final[str] = "5325547803936572038"
AI_CHATBOT_CUSTOM_EMOJI_ID: Final[str] = "5573451671289200650"
AI_GENERATING_EMOJI_ID: Final[str] = "5573333417954639880"
AI_PROGRESS_MARKER: Final[str] = "💭"
AI_PROGRESS_LINE_EMOJI_IDS: Final[tuple[str, str, str]] = (
    "5348210173104134595",
    "5350601434800889611",
    "5348267111485581196",
)
_LOW_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5841410188350852356"
_MIDDLE_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5841424383217766066"
_HIGH_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5841233274352963797"
AI_BATTERY_CUSTOM_EMOJI_IDS: Final[frozenset[str]] = frozenset(
    {_LOW_BATTERY_CUSTOM_EMOJI_ID, _MIDDLE_BATTERY_CUSTOM_EMOJI_ID, _HIGH_BATTERY_CUSTOM_EMOJI_ID}
)


class _LineBreak(Element):
    def to_html(self, *_args: Any) -> str:
        return "<br>"

    def to_rich(self) -> str:
        return "<br>"

    def to_md(self) -> str:
        return "\n"


class _InlineElement(Element):
    def __init__(self, element: Element) -> None:
        self.element = element

    def to_html(self, *_args: Any) -> str:
        return self.element.to_html()

    def to_rich(self) -> str:
        return self.element.to_rich().replace("<p>", "").replace("</p>", "")

    def to_md(self) -> str:
        return self.element.to_md()


def _inline_body_item(item: Element | str | None) -> Element | str | None:
    return _InlineElement(item) if isinstance(item, Element) else item


def _get_battery_custom_emoji_id(percentage: int) -> str:
    if percentage >= 66:
        return _HIGH_BATTERY_CUSTOM_EMOJI_ID
    if percentage >= 33:
        return _MIDDLE_BATTERY_CUSTOM_EMOJI_ID
    return _LOW_BATTERY_CUSTOM_EMOJI_ID


def _battery_custom_emoji(percentage: int) -> Element:
    return CustomEmoji(_get_battery_custom_emoji_id(percentage), "🔋")


AIHeaderStyle = Literal["disable", "simple"]
AIHeaderPurpose = Literal["chatbot", "filters", "translation", "summary"]

_HEADER_STYLE_FLAG_BY_PURPOSE: Final[dict[AIHeaderPurpose, FeatureType]] = {
    "filters": "ai_filters_header_style",
    "translation": "ai_translations_header_style",
    "summary": "ai_chat_summaries_header_style",
}


async def get_ai_header_style(purpose: AIHeaderPurpose, chat_tid: int, *, redis: Redis) -> AIHeaderStyle:
    if purpose == "chatbot":
        return "simple"
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
    tool_labels: Sequence[AITool] = (),
    emoji_id: str = AI_CUSTOM_EMOJI_ID,
) -> Doc:
    inline_body = tuple(_inline_body_item(item) for item in body)
    if header is None:
        return Doc(*inline_body)
    tools = (
        HList("(", HList(*(tool.display_label() for tool in tool_labels), divider=", "), ")", divider="")
        if tool_labels
        else None
    )
    return Doc(
        HList(
            HList(
                CustomEmoji(emoji_id, AI_EMOJI),
                tools,
                *inline_body,
                divider=" ",
            ),
            _LineBreak(),
            Paragraph(header),
            divider="",
        )
    )


def build_ai_progress_doc(
    body: Element | str,
    status: Element | None = None,
    *,
    reasoning: Element | None = None,
) -> Doc:
    footer = HList(*(CustomEmoji(emoji_id, "〰️") for emoji_id in AI_PROGRESS_LINE_EMOJI_IDS), divider="")
    return Doc(
        HList(
            HList(CustomEmoji(AI_GENERATING_EMOJI_ID, AI_PROGRESS_MARKER), _inline_body_item(body), divider=" "),
            _LineBreak(),
            Italic(reasoning) if reasoning is not None else None,
            _LineBreak() if reasoning is not None else None,
            status,
            _LineBreak() if status is not None else None,
            footer,
            divider="",
        )
    )


def ai_credit_header(percentage: int, model_label: str | None = None) -> Element:
    model = f"({model_label})" if model_label else None
    return HList(_battery_custom_emoji(percentage), str(percentage) + "%", model, divider=" ")
