from types import SimpleNamespace

from sophie_bot.modules.ai.utils.self_reply import cut_titlebar, is_ai_message, message_text


def test_is_ai_message_accepts_legacy_header() -> None:
    assert is_ai_message("[✨ Sophie AI] [Gemini 3 Flash] hello")


def test_is_ai_message_accepts_short_header() -> None:
    assert is_ai_message("[✨ AI] Gemini 3 Flash | Search 🔍 | 🔋 99%")


def test_cut_titlebar_removes_short_header() -> None:
    assert cut_titlebar("[✨ AI] Gemini 3 Flash | Search 🔍 | 🔋 99%\nHello") == "Hello"


def test_is_ai_message_accepts_the_table_header() -> None:
    assert is_ai_message("✨ AI | Help 📖 | 🔋 80%")
    assert is_ai_message("✨ AI")


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
