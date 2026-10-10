from collections.abc import Sequence
from typing import Any, Final

from stfu_tg import BlockQuote, CustomEmoji, Doc, HList, Italic, Paragraph
from stfu_tg.doc import Element

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.utils.ai_tool import AITool

AI_CUSTOM_EMOJI_ID: Final[str] = "5325547803936572038"
AI_CHATBOT_CUSTOM_EMOJI_ID: Final[str] = "5573451671289200650"
AI_GENERATING_EMOJI_ID: Final[str] = "5573333417954639880"
AI_PROGRESS_MARKER: Final[str] = "💭"
AI_REASONING_EMOJI_ID: Final[str] = "5537353471893700616"
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
        rendered = self.element.to_rich()
        if rendered.startswith("<p>"):
            return rendered[3:].replace("</p>", "", 1)
        return rendered

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


def build_ai_message_doc(
    header: Element | str | None,
    *body: Element | str | None,
    tool_labels: Sequence[AITool] = (),
    emoji_id: str = AI_CUSTOM_EMOJI_ID,
) -> Doc:
    if header is None:
        return Doc(*body)
    body_items = tuple(item for item in body if item)
    inline_body = (_inline_body_item(body_items[0]), *body_items[1:]) if body_items else ()
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
    activity_history: Sequence[Element | str] = (),
) -> Doc:
    footer = HList(*(CustomEmoji(emoji_id, "〰️") for emoji_id in AI_PROGRESS_LINE_EMOJI_IDS), divider="")
    return Doc(
        HList(
            HList(
                CustomEmoji(AI_GENERATING_EMOJI_ID, AI_PROGRESS_MARKER),
                _inline_body_item(body) if body else None,
                divider=" ",
            ),
            _LineBreak(),
            (
                BlockQuote(
                    HList(
                        CustomEmoji(AI_REASONING_EMOJI_ID, AI_PROGRESS_MARKER),
                        Italic(_InlineElement(reasoning)),
                        divider=" ",
                    )
                )
                if reasoning is not None
                else None
            ),
            _LineBreak() if reasoning is not None else None,
            status,
            _LineBreak() if status is not None else None,
            HList(
                *(HList(Italic(label), _LineBreak(), divider="") for label in activity_history),
                divider="",
            )
            if activity_history
            else None,
            footer,
            divider="",
        )
    )


def ai_credit_header(percentage: int, model_label: str | None = None) -> Element:
    model = f"({model_label})" if model_label else None
    return HList(_battery_custom_emoji(percentage), str(percentage) + "%", model, divider=" ")
