from aiogram.fsm.state import State, StatesGroup
from stfu_tg import Template

from sophie_bot.constants import AI_EMOJI
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class AiPMFSM(StatesGroup):
    in_ai = State()


AI_PM_STOP_TEXT = l_("🛑 Exit AI mode")
AI_PM_STOP_HELP_TEXT = l_("🛑 Exit AI help")
AI_PM_RESET = l_("🔄 Reset AI context")
AI_PM_NORMAL_MODE = l_("💬 Switch to normal AI mode")
AI_PM_HELP_MODE = l_("📖 Sophie help mode")

AI_GENERATED_TEXT = l_(lambda: Template(_("{ai_emoji} Sophie AI"), ai_emoji=AI_EMOJI).to_html())
