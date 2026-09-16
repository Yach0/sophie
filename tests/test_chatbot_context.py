import datetime

from sophie_bot.modules.ai.utils.chatbot_context import _base_chatbot_instruction_doc


def test_base_chatbot_instruction_labels_runtime_as_utc() -> None:
    today = datetime.datetime(2026, 9, 16, 12, 34, tzinfo=datetime.UTC)

    instructions = _base_chatbot_instruction_doc("system prompt", today).to_md()

    assert "Today is 16 September 2026, 12:34 (UTC)" in instructions
