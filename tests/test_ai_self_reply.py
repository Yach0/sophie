import re
from types import SimpleNamespace

import pytest
from aiogram.types import Message
from stfu_tg import Doc

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.handlers.reply import AiReplyHandler
from sophie_bot.modules.ai.utils.ai_header import (
    AI_CHATBOT_CUSTOM_EMOJI_ID,
    AI_CUSTOM_EMOJI_ID,
    AI_GENERATING_EMOJI_ID,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_progress import ai_progress_line, random_ai_thinking_text
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME
from sophie_bot.modules.ai.utils.help_tip import build_help_mode_tip
from sophie_bot.modules.ai.utils.self_reply import cut_titlebar, is_ai_message, message_text


def _as_telegram_shows(html: str) -> str:
    """Telegram delivers custom emoji as their fallback character, with the markup stripped."""
    return re.sub(r"<[^>]+>", "", html)


def test_is_ai_message_accepts_legacy_header() -> None:
    assert is_ai_message("[✨ Sophie AI] [Gemini 3 Flash] hello")


def test_is_ai_message_accepts_short_header() -> None:
    assert is_ai_message("[✨ AI] Gemini 3 Flash | Search 🔍 | 🔋 99%")


def test_cut_titlebar_removes_short_header() -> None:
    assert cut_titlebar("[✨ AI] Gemini 3 Flash | Search 🔍 | 🔋 99%\nHello") == "Hello"


def test_simple_header_is_detected_and_removed_without_losing_multiline_body() -> None:
    text = "✨ 🔋 99% Hello\nsecond line"

    assert is_ai_message(text)
    assert cut_titlebar(text) == "Hello\nsecond line"


def test_simple_header_without_battery_preserves_body_starting_with_percentage() -> None:
    text = "✨ 🔋\n100% of the work is complete"

    assert is_ai_message(text)
    assert cut_titlebar(text) == "100% of the work is complete"


def test_simple_footer_is_removed_after_multiline_body() -> None:
    text = "✨ Response text\nMight be many lines\n123\n🔋 90%"

    assert is_ai_message(text)
    assert cut_titlebar(text) == "Response text\nMight be many lines\n123"


def test_rendered_simple_header_is_removed_from_cached_multiline_body() -> None:
    body = "Hello there! How are you?\nNew line test text\nThird line"
    header = build_ai_header("simple", ai_credit_header(95))

    doc = build_ai_message_doc(header, body)

    assert doc.to_rich() == (
        f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> {body}'
        '<br><p><tg-emoji emoji-id="5841233274352963797">🔋</tg-emoji> 95%</p>'
    )
    assert cut_titlebar(doc.to_md()) == body


def test_rendered_tool_labels_and_model_footer_are_removed_from_cached_body() -> None:
    body = "Reply here"
    tool_labels = (AI_TOOLS_BY_NAME["web_search"], AI_TOOLS_BY_NAME["get_notes"])
    header = build_ai_header("simple", ai_credit_header(45, "Gemini 5"))
    doc = build_ai_message_doc(header, body, tool_labels=tool_labels)

    text = doc.to_md()

    assert is_ai_message(text)
    assert cut_titlebar(text, tool_labels=tool_labels) == body


def test_old_custom_battery_markup_is_removed_from_cached_body() -> None:
    text = (
        "✨ Hello there! How are you?\nNew line test text\nThird line\n"
        '<tg-emoji emoji-id="5816915599019741395">🔋</tg-emoji> 95%'
    )

    assert cut_titlebar(text) == "Hello there! How are you?\nNew line test text\nThird line"


def test_old_rich_battery_footer_is_not_reused_as_reply_context() -> None:
    message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": [
                            {"type": "custom_emoji", "custom_emoji_id": AI_CUSTOM_EMOJI_ID, "alternative_text": "✨"},
                            " Hello",
                        ],
                    },
                    {
                        "type": "paragraph",
                        "text": [
                            {"type": "custom_emoji", "custom_emoji_id": "5816915599019741395", "alternative_text": "🔋"},
                            " 95%",
                        ],
                    },
                ]
            },
        }
    )

    assert cut_titlebar(message) == "Hello"


def test_help_tip_after_simple_footer_is_excluded_from_cached_answer() -> None:
    doc = build_ai_message_doc(build_ai_header("simple", ai_credit_header(92)), "test")
    doc += build_help_mode_tip()

    assert cut_titlebar(doc.to_md()) == "test"


def test_same_line_tip_after_footer_is_excluded_from_cached_answer() -> None:
    assert cut_titlebar("✨ test\n🔋 92% ⚠️ Help mode is available.") == "test"


def test_battery_mention_in_body_is_not_an_ai_footer() -> None:
    assert not is_ai_message("✨ Battery status\n🔋 92% of charge remains")


@pytest.mark.parametrize(
    ("emoji_id", "fallback"),
    [
        (AI_CUSTOM_EMOJI_ID, "✨"),
        (AI_CHATBOT_CUSTOM_EMOJI_ID, "✨"),
        (AI_GENERATING_EMOJI_ID, "💭"),
    ],
)
def test_rich_ai_marker_identity_triggers_without_battery_footer(emoji_id: str, fallback: str) -> None:
    message = Message.model_validate(
        {
            "message_id": 3084553,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": [
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": emoji_id,
                                "alternative_text": fallback,
                            },
                            " Answer without a battery footer",
                        ],
                    }
                ]
            },
        }
    )

    assert is_ai_message(message)
    assert not is_ai_message(message.model_copy(update={"rich_message": None}))


def test_other_rich_emoji_with_same_fallback_is_not_ai_marker() -> None:
    message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": [
                            {"type": "custom_emoji", "custom_emoji_id": "123", "alternative_text": "✨"},
                            " Lookalike ",
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": AI_CUSTOM_EMOJI_ID,
                                "alternative_text": "✨",
                            },
                            "\n🔋 90%",
                        ],
                    }
                ]
            },
        }
    )

    assert not is_ai_message(message)


@pytest.mark.parametrize(
    "battery_id",
    ("5819177212833697095", "5818860416045945285", "5816915599019741395"),
)
def test_rich_battery_and_all_trailing_metadata_are_removed(battery_id: str) -> None:
    message = Message.model_validate(
        {
            "message_id": 3084553,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": [
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": AI_CUSTOM_EMOJI_ID,
                                "alternative_text": "✨",
                            },
                            " Answer contains ",
                            {"type": "custom_emoji", "custom_emoji_id": battery_id, "alternative_text": "🔋"},
                            " 42% as text.\n",
                            {"type": "custom_emoji", "custom_emoji_id": battery_id, "alternative_text": "🔋"},
                            " 92% ⚠️ Help mode is available.",
                        ],
                    },
                    {"type": "paragraph", "text": ["Extra content after the battery"]},
                ]
            },
        }
    )

    assert cut_titlebar(message) == "Answer contains 🔋 42% as text."


async def test_reply_handler_requires_sophie_as_sender() -> None:
    rich_message = Message.model_validate(
        {
            "message_id": 3084553,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "from": {"id": 483808054, "is_bot": False, "first_name": "yachu"},
            "rich_message": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": [
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": AI_CUSTOM_EMOJI_ID,
                                "alternative_text": "✨",
                            },
                            " Forwarded Sophie answer",
                        ],
                    }
                ]
            },
        }
    )

    assert not await AiReplyHandler.filter(SimpleNamespace(reply_to_message=rich_message))
    sophie = rich_message.from_user.model_copy(update={"id": CONFIG.bot_id})
    assert await AiReplyHandler.filter(
        SimpleNamespace(reply_to_message=rich_message.model_copy(update={"from_user": sophie}))
    )
    untagged_reply = rich_message.model_copy(
        update={"from_user": sophie, "rich_message": None, "text": "✨ Old answer\n🔋 90%"}
    )
    assert not await AiReplyHandler.filter(SimpleNamespace(reply_to_message=untagged_reply))


def test_disabled_header_text_is_not_mistaken_for_an_ai_message() -> None:
    text = "A header-free answer\nwith another line"

    assert not is_ai_message(text)
    assert cut_titlebar(text) == text


def test_is_ai_message_accepts_the_table_header() -> None:
    assert is_ai_message("✨ AI | Help 📖 | 🔋 80%")
    assert is_ai_message("✨ AI")


def test_is_ai_message_accepts_the_in_progress_placeholder() -> None:
    """The progress marker identifies an answer before its completed-message markers exist."""
    placeholder = _as_telegram_shows(Doc(ai_progress_line(random_ai_thinking_text())).to_html())

    assert is_ai_message(placeholder)


def test_is_ai_message_accepts_a_placeholder_that_already_streamed_text() -> None:
    placeholder = _as_telegram_shows(Doc(ai_progress_line("Searching the web...")).to_html())

    assert is_ai_message(f"{placeholder}\n\nHere is what I found")


def test_is_ai_message_rejects_other_ai_titled_replies() -> None:
    """Replying to /aiusage or an AI error must not start a conversation."""
    assert not is_ai_message("✨ AI Usage")
    assert not is_ai_message("✨ AI Response")
    assert not is_ai_message("hello")


def test_message_text_reads_the_header_out_of_a_rich_table() -> None:
    """A rich message carries no text, and the header now lives in table cells."""
    table = SimpleNamespace(
        cells=[
            [
                SimpleNamespace(text="✨ AI"),
                SimpleNamespace(text="Help 📖"),
                SimpleNamespace(text="🔋 80%"),
            ]
        ]
    )
    body = SimpleNamespace(text="Notes are saved with /save.")
    message = SimpleNamespace(text=None, rich_message=SimpleNamespace(blocks=[table, body]))

    text = message_text(message)

    assert text.splitlines()[0] == "✨ AI | Help 📖 | 🔋 80%"
    assert is_ai_message(text)


def test_message_text_reads_inline_simple_rich_message() -> None:
    message = SimpleNamespace(
        text=None,
        rich_message=SimpleNamespace(blocks=[SimpleNamespace(text=["✨ 🔋 80% ", "Answer"])]),
    )

    text = message_text(message)

    assert text == "✨ 🔋 80% Answer"
    assert is_ai_message(text)
    assert cut_titlebar(text) == "Answer"


def test_message_text_prefers_rich_content_over_plain_fallback() -> None:
    message = SimpleNamespace(
        text="Answer",
        rich_message=SimpleNamespace(blocks=[SimpleNamespace(text=["✨ Answer"]), SimpleNamespace(text="🔋 80%")]),
    )

    text = message_text(message)

    assert text == "✨ Answer\n🔋 80%"
    assert is_ai_message(text)


def _compact_heading_ai_message() -> SimpleNamespace:
    ai_emoji = SimpleNamespace(alternative_text="✨")
    battery_emoji = SimpleNamespace(alternative_text="🔋")
    return SimpleNamespace(
        text=None,
        rich_message=SimpleNamespace(
            blocks=[
                SimpleNamespace(text=[ai_emoji, " "]),
                SimpleNamespace(text="Answer heading"),
                SimpleNamespace(text="Answer body"),
                SimpleNamespace(text=[battery_emoji, " 80%"]),
            ]
        ),
    )


def test_message_text_detects_simple_ai_message_split_by_rich_heading() -> None:
    text = message_text(_compact_heading_ai_message())

    assert text == "✨ \nAnswer heading\nAnswer body\n🔋 80%"
    assert is_ai_message(text)
    assert cut_titlebar(text) == "Answer heading\nAnswer body"


def test_message_text_leaves_disabled_rich_message_body_unchanged() -> None:
    message = SimpleNamespace(
        text=None,
        rich_message=SimpleNamespace(blocks=[SimpleNamespace(text="Header-free answer")]),
    )

    text = message_text(message)

    assert text == "Header-free answer"
    assert not is_ai_message(text)
    assert cut_titlebar(text) == text


def test_cut_titlebar_preserves_every_table_body_line() -> None:
    text = "✨ AI | Help 📖 | 🔋 80%\nFirst line\nSecond line"

    assert cut_titlebar(text) == "First line\nSecond line"
