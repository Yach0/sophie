from types import SimpleNamespace

import pytest
from aiogram.types import (
    Message,
    RichBlockParagraph,
    RichBlockUnion,
    RichMessage,
    RichTextCustomEmoji,
    RichTextItalic,
    User,
)

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.handlers.reply import AiReplyHandler
from sophie_bot.modules.ai.utils.ai_header import (
    AI_BATTERY_CUSTOM_EMOJI_IDS,
    AI_CHATBOT_CUSTOM_EMOJI_ID,
    AI_CUSTOM_EMOJI_ID,
    AI_GENERATING_EMOJI_ID,
)
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME
from sophie_bot.modules.ai.utils.self_reply import cut_titlebar, is_ai_message
from sophie_bot.shared.message_text import message_text


def _rich_message(*blocks: RichBlockUnion) -> Message:
    return Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": RichMessage(blocks=list(blocks)),
        }
    )


def _answer_paragraph(body: str) -> RichBlockParagraph:
    return RichBlockParagraph(
        text=[RichTextCustomEmoji(custom_emoji_id=AI_CUSTOM_EMOJI_ID, alternative_text="✨"), " " + body]
    )


def _battery_footer(emoji_id: str, suffix: str = " 95%") -> RichBlockParagraph:
    return RichBlockParagraph(
        text=[RichTextCustomEmoji(custom_emoji_id=emoji_id, alternative_text="🔋"), suffix]
    )


@pytest.mark.parametrize(
    ("emoji_id", "fallback"),
    [
        (AI_CUSTOM_EMOJI_ID, "✨"),
        (AI_CHATBOT_CUSTOM_EMOJI_ID, "✨"),
        (AI_GENERATING_EMOJI_ID, "💭"),
    ],
)
def test_rich_ai_marker_identity_triggers_without_battery_footer(emoji_id: str, fallback: str) -> None:
    message = _rich_message(
        RichBlockParagraph(
            text=[RichTextCustomEmoji(custom_emoji_id=emoji_id, alternative_text=fallback), " Answer"]
        )
    )

    assert is_ai_message(message)
    assert cut_titlebar(message) == "Answer"
    assert not is_ai_message(message.model_copy(update={"rich_message": None}))


def test_other_rich_emoji_with_same_fallback_is_not_ai_marker() -> None:
    message = _rich_message(
        RichBlockParagraph(
            text=[
                RichTextCustomEmoji(custom_emoji_id="123", alternative_text="✨"),
                " Lookalike ",
                RichTextCustomEmoji(custom_emoji_id=AI_CUSTOM_EMOJI_ID, alternative_text="✨"),
                "\n🔋 90%",
            ]
        )
    )

    assert not is_ai_message(message)
    assert cut_titlebar(message) == message_text(message)


def test_plain_message_is_not_interpreted_as_ai_display_decoration() -> None:
    message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "text": "✨ Battery status\n🔋 92% of charge remains",
        }
    )

    assert not is_ai_message(message)
    assert cut_titlebar(message) == message.text


@pytest.mark.parametrize("battery_id", sorted(AI_BATTERY_CUSTOM_EMOJI_IDS))
def test_current_rich_battery_and_trailing_decoration_are_removed(battery_id: str) -> None:
    body = "Response text\nMore lines\n123"
    message = _rich_message(
        _answer_paragraph(body),
        _battery_footer(battery_id, " 95% (Model name)"),
        RichBlockParagraph(text=RichTextItalic(text="Additional display decoration")),
    )

    assert cut_titlebar(message) == body


def test_rich_body_battery_mentions_are_not_footer_markers() -> None:
    battery_id = min(AI_BATTERY_CUSTOM_EMOJI_IDS)
    message = _rich_message(
        _answer_paragraph("Battery status"),
        _battery_footer(battery_id, " 42% of charge remains"),
        RichBlockParagraph(text="🔋 90% of charge remains"),
        _battery_footer(battery_id),
    )

    assert cut_titlebar(message) == "Battery status\n🔋 42% of charge remains\n🔋 90% of charge remains"
    assert message.rich_message is not None
    without_footer = message.model_copy(
        update={"rich_message": RichMessage(blocks=message.rich_message.blocks[:-1])}
    )
    assert cut_titlebar(without_footer) == cut_titlebar(message)


def test_current_plain_battery_footer_is_removed_from_rich_answer() -> None:
    message = _rich_message(_answer_paragraph("Answer"), RichBlockParagraph(text="🔋"))

    assert cut_titlebar(message) == "Answer"


def test_rich_body_whitespace_is_preserved() -> None:
    body = "\nAnswer\n\nDetails\n"
    message = _rich_message(
        _answer_paragraph(body),
        _battery_footer(min(AI_BATTERY_CUSTOM_EMOJI_IDS)),
    )

    assert cut_titlebar(message) == body


def test_rich_header_only_paragraph_does_not_add_a_body_line() -> None:
    message = _rich_message(
        _answer_paragraph(""),
        RichBlockParagraph(text="Answer heading"),
        RichBlockParagraph(text="Answer body"),
        _battery_footer(min(AI_BATTERY_CUSTOM_EMOJI_IDS)),
    )

    assert cut_titlebar(message) == "Answer heading\nAnswer body"


def test_tool_labels_are_removed_only_with_explicit_rich_header_metadata() -> None:
    tool_labels = (AI_TOOLS_BY_NAME["web_search"], AI_TOOLS_BY_NAME["get_notes"])
    labels = f"({', '.join(tool.display_label() for tool in tool_labels)})"
    message = _rich_message(
        _answer_paragraph(labels + " Answer"),
        _battery_footer(min(AI_BATTERY_CUSTOM_EMOJI_IDS)),
    )

    assert cut_titlebar(message, tool_labels=tool_labels) == "Answer"
    assert cut_titlebar(message) == labels + " Answer"
    user_message = _rich_message(RichBlockParagraph(text=labels + " User text"))
    assert cut_titlebar(user_message, tool_labels=tool_labels) == labels + " User text"


async def test_reply_handler_requires_sophie_as_sender() -> None:
    rich_message = _rich_message(_answer_paragraph("Answer")).model_copy(
        update={"from_user": User(id=483808054, is_bot=False, first_name="User")}
    )

    assert not await AiReplyHandler.filter(SimpleNamespace(pinned_message=None, reply_to_message=rich_message))
    assert rich_message.from_user is not None
    sophie = rich_message.from_user.model_copy(update={"id": CONFIG.bot_id})
    sophie_message = rich_message.model_copy(update={"from_user": sophie})
    assert await AiReplyHandler.filter(SimpleNamespace(pinned_message=None, reply_to_message=sophie_message))
    assert not await AiReplyHandler.filter(
        SimpleNamespace(pinned_message=sophie_message, reply_to_message=sophie_message)
    )
    untagged_reply = sophie_message.model_copy(update={"rich_message": None, "text": "Answer"})
    assert not await AiReplyHandler.filter(SimpleNamespace(pinned_message=None, reply_to_message=untagged_reply))
