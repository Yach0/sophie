import datetime

from sophie_bot.modules.ai.utils.modern_context import _base_chatbot_instruction_doc


def test_chatbot_instruction_prefix_is_stable_until_the_local_date_changes() -> None:
    timezone = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
    today = datetime.datetime(2026, 9, 16, 12, 34, tzinfo=timezone)
    later_today = today.replace(hour=23, minute=59, second=59)
    tomorrow = today + datetime.timedelta(days=1)

    instructions = _base_chatbot_instruction_doc("system prompt", today).to_md()

    assert instructions == _base_chatbot_instruction_doc("system prompt", later_today).to_md()
    assert instructions != _base_chatbot_instruction_doc("system prompt", tomorrow).to_md()
