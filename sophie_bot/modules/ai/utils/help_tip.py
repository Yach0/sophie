from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from stfu_tg import Doc, Italic

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.callbacks import AIChatCallback, AIHelpStartUrlCallback
from sophie_bot.modules.ai.utils.sophie_inspect import is_sophie_inspect_chat
from sophie_bot.utils.i18n import gettext as _

_HELP_TOOL_NAME = "sophie_help"


def _used_help_tool(message_history: list[ModelRequest | ModelResponse]) -> bool:
    return any(
        isinstance(part, ToolCallPart) and part.tool_name == _HELP_TOOL_NAME
        for message in message_history
        for part in message.parts
    )


async def should_offer_help_mode(
    message: Message, mode: AIMode, message_history: list[ModelRequest | ModelResponse]
) -> bool:
    """Whether to point the user at the Sophie-help assistant after a documentation answer.

    Not when they are already in it, and not where source inspection is available, since that chat
    can already answer more than the documentation does.
    """
    if mode is AIMode.sophie_help or not _used_help_tool(message_history):
        return False
    return not await is_sophie_inspect_chat(message.chat.id)


def build_help_mode_tip() -> Doc:
    return Doc(Italic(_("⚠️ If you want more detailed help, use the special Sophie help mode.")))


def build_help_mode_keyboard(message: Message) -> InlineKeyboardMarkup:
    """A private chat can enter the mode directly; a group has to send the user to the bot's PM."""
    text = _("Sophie help mode")
    if message.chat.type == ChatType.PRIVATE:
        button = InlineKeyboardButton(text=text, callback_data=AIChatCallback().pack())
    else:
        button = InlineKeyboardButton(text=text, url=AIHelpStartUrlCallback().pack())
    return InlineKeyboardMarkup(inline_keyboard=[[button]])
