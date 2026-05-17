from sophie_bot.modules.ai.utils.self_reply import cut_titlebar, is_ai_message


def test_is_ai_message_accepts_legacy_header() -> None:
    assert is_ai_message("[✨ Sophie AI] [Gemini 3 Flash] hello")


def test_is_ai_message_accepts_short_header() -> None:
    assert is_ai_message("[✨ AI] Gemini 3 Flash | Search 🔍 | 🔋 99%")


def test_cut_titlebar_removes_short_header() -> None:
    assert cut_titlebar("[✨ AI] Gemini 3 Flash | Search 🔍 | 🔋 99%\nHello") == "Hello"
