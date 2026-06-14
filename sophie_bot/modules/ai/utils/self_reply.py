from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT

# Short header produced by build_chatbot_header: "[✨ AI] ..."
# Must include the closing "]" so "[✨ AI Usage]" / "[✨ AI Response]" don't match.
_AI_SHORT_GENERATED_TEXT = f"{AI_EMOJI} AI]"


def is_ai_message(text: str) -> bool:
    if not text.startswith("["):
        return False
    normalized_text = text[1:]
    return normalized_text.startswith((str(AI_GENERATED_TEXT), _AI_SHORT_GENERATED_TEXT))


def cut_titlebar(text: str) -> str:
    lines = text.split("\n")
    return lines[1] if len(lines) > 1 else ""
