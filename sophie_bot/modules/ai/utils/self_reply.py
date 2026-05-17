from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT

_AI_SHORT_GENERATED_TEXT = f"{AI_EMOJI} AI"


def is_ai_message(text: str) -> bool:
    normalized_text = text.removeprefix("[")
    return normalized_text.startswith((str(AI_GENERATED_TEXT), _AI_SHORT_GENERATED_TEXT))


def cut_titlebar(text: str) -> str:
    lines = text.split("\n")
    return lines[1] if len(lines) > 1 else ""
