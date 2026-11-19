from typing import Literal

from aiogram.filters.callback_data import CallbackData

from sophie_bot.filters.command_start import CmdStart


class AIResetContext(CallbackData, prefix="ai_reset_context"):
    pass


class AIModeCallback(CallbackData, prefix="ai_mode"):
    mode: str


class AIChatCallback(CallbackData, prefix="ai_chat"):
    pass


class AIModeratorEnabledCallback(CallbackData, prefix="ai_mod_enabled"):
    pass


class AIModeratorCategoryCallback(CallbackData, prefix="ai_mod_cat"):
    category: str


class AIHelpStartUrlCallback(CmdStart, prefix="aihelp"):
    """Deep link from a group into the Sophie-help assistant in the bot's private chat."""


MemoryListView = Literal["show", "history", "sources"]


class AIMemoryPageCallback(CallbackData, prefix="aim"):
    token: str
    view: MemoryListView
    page: int = 0
    target: str = ""
    part: int = 0


class AIMemoryClearCallback(CallbackData, prefix="aim_clear"):
    owner_tid: int
    operation: Literal["forget", "off"]
    token: str
    action: Literal["confirm", "cancel"]


class AIMemoryEditCallback(CallbackData, prefix="aim_edit"):
    token: str
    action: Literal["confirm", "cancel"]
