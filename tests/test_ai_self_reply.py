import re
from types import SimpleNamespace

from stfu_tg import Doc

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.handlers.reply import AiReplyHandler
from sophie_bot.modules.ai.utils.ai_header import (
    AI_CUSTOM_EMOJI_ID,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_progress import ai_progress_line, random_ai_thinking_text
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
        '<br><tg-emoji emoji-id="5816915599019741395">🔋</tg-emoji> 95%'
    )
    assert cut_titlebar(doc.to_md()) == body


def test_rendered_tool_labels_and_model_footer_are_removed_from_cached_body() -> None:
    body = "Reply here"
    tool_labels = ("🔍 Internet Search", "📝 Notes")
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
        from_user=SimpleNamespace(id=CONFIG.bot_id),
    )


def test_message_text_detects_simple_ai_message_split_by_rich_heading() -> None:
    text = message_text(_compact_heading_ai_message())

    assert text == "✨ \nAnswer heading\nAnswer body\n🔋 80%"
    assert is_ai_message(text)
    assert cut_titlebar(text) == "Answer heading\nAnswer body"


async def test_reply_handler_accepts_ai_message_split_by_rich_heading() -> None:
    message = SimpleNamespace(reply_to_message=_compact_heading_ai_message())

    assert await AiReplyHandler.filter(message)


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
