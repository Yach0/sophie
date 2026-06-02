from __future__ import annotations

from pydantic_ai.messages import ModelResponse, TextPart

from sophie_bot.modules.ai.utils.message_history import AIMessageHistory, AIUserMessageFormatter


def test_user_message_formatter_sanitizes_names_and_replies() -> None:
    rendered = AIUserMessageFormatter.user_message(
        "hello",
        name="<Alice!>",
        reply_to_user="Bob@example",
    )

    assert rendered == "Alice: From Alice, as reply to Bobexample: hello"


def test_message_history_adds_system_custom_and_debug_output() -> None:
    history = AIMessageHistory()

    history.add_system("system prompt")
    history.add_custom("user prompt", name="Tester")
    history.prompt = ["current prompt"]
    debug_doc = history.history_debug()

    rendered = str(debug_doc)
    assert "system prompt" in rendered
    assert "Tester: user prompt" in rendered
    assert "current prompt" in rendered


def test_message_history_moderation_extracts_text_roles() -> None:
    history = AIMessageHistory()
    history.add_system("system prompt")
    history.add_custom("user prompt", name="Tester")
    history.message_history.append(ModelResponse(parts=[TextPart(content="assistant reply")]))
    history.prompt = ["current prompt"]

    assert history.to_moderation == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Tester: user prompt"},
        {"role": "assistant", "content": "assistant reply"},
        {"role": "user", "content": "current prompt"},
    ]
