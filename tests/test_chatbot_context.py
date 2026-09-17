import datetime

from sophie_bot.modules.ai.utils.chatbot_context import _base_chatbot_instruction_doc


def test_base_chatbot_instruction_uses_runtime_timezone_label() -> None:
    timezone = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
    today = datetime.datetime(2026, 9, 16, 12, 34, tzinfo=timezone)

    instructions = _base_chatbot_instruction_doc("system prompt", today).to_md()

    assert "Today is 16 September 2026, 12:34 (IST)" in instructions
