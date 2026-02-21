from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT


def is_ai_message(text: str) -> bool:
    return text.removeprefix("[").startswith(str(AI_GENERATED_TEXT))


def cut_titlebar(text: str) -> str:
    lines = text.split("\n")
    return lines[1] if len(lines) > 1 else ""
