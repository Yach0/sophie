from aiogram.filters.callback_data import CallbackData

from sophie_bot.filters.command_start import CmdStart


class AIResetContext(CallbackData, prefix="ai_reset_context"):
    pass


class AIModeCallback(CallbackData, prefix="ai_mode"):
    mode: str


class AIChatCallback(CallbackData, prefix="ai_chat"):
    pass


class AIModeratorCategoryCallback(CallbackData, prefix="ai_mod_cat"):
    category: str


class AIModeratorToggleCallback(CallbackData, prefix="ai_mod_toggle"):
    action: str


class AIModeratorConfirmCallback(CallbackData, prefix="ai_mod_confirm"):
    pass


class AIHelpStartUrlCallback(CmdStart, prefix="aihelp"):
    """Deep link from a group into the Sophie-help assistant in the bot's private chat."""
