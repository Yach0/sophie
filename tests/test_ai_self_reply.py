import re
from types import SimpleNamespace

from stfu_tg import Doc

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


def test_disabled_header_text_is_not_mistaken_for_an_ai_message() -> None:
    text = "A header-free answer\nwith another line"

    assert not is_ai_message(text)
    assert cut_titlebar(text) == text


def test_is_ai_message_accepts_the_table_header() -> None:
    assert is_ai_message("✨ AI | Help 📖 | 🔋 80%")
    assert is_ai_message("✨ AI")


def test_is_ai_message_accepts_the_in_progress_placeholder() -> None:
    """Replying while the answer is still being generated must continue the conversation.

    The placeholder carries no table header, so the progress marker is what identifies it.
    """
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
