from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage, Message
from stfu_tg import Doc, RichTable, RichTableCell, Title

from sophie_bot.constants import AI_EMOJI
from sophie_bot.db.models.ai.ai_mode import SELECTABLE_MODES, AIMode
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.callbacks import AIModeCallback
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode, set_chat_mode
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

MODE_TITLES: Mapping[AIMode, LazyProxy] = {
    AIMode.disabled: l_("🚫 Disabled"),
    AIMode.entertainment: l_("🎉 Entertainment"),
    AIMode.moderation: l_("🛡 Moderation"),
    AIMode.support: l_("💬 Support"),
}

MODE_DESCRIPTIONS: Mapping[AIMode, LazyProxy] = {
    AIMode.disabled: l_("Every AI feature is off."),
    AIMode.entertainment: l_("Chats along, joins conversations on its own and remembers people. Reduced privacy."),
    AIMode.moderation: l_(
        "Only moderates. No chatbot, and no message history is kept at all. Best in class privacy protection."
    ),
    AIMode.support: l_("Answers questions using the chat notes, and moderates."),
}


def _build_table() -> RichTable:
    return RichTable(
        [RichTableCell(_("Mode"), is_header=True), RichTableCell(_("What Sophie does"), is_header=True)],
        *(
            [RichTableCell(str(MODE_TITLES[mode])), RichTableCell(str(MODE_DESCRIPTIONS[mode]))]
            for mode in SELECTABLE_MODES
        ),
        bordered=True,
    )


def _build_keyboard(selected: AIMode) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{MODE_TITLES[mode]}{' ✅' if mode == selected else ''}",
                    callback_data=AIModeCallback(mode=mode.value).pack(),
                )
            ]
            for mode in SELECTABLE_MODES
        ]
    )


def _build_doc() -> Doc:
    return Doc(Title(f"{AI_EMOJI} {_('AI Mode')}"), _build_table())


async def _send_picker(message: Message, selected: AIMode) -> None:
    doc = _build_doc()
    keyboard = _build_keyboard(selected)
    try:
        await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            rich_message=InputRichMessage(html=doc.to_rich()),
            reply_markup=keyboard,
        )
    except TelegramAPIError:
        await message.reply(doc.to_html(), reply_markup=keyboard)


@flags.help(description=l_("Select what the AI does in this chat"))
class AIModeSetting(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("aimode"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        await _send_picker(self.event, await get_chat_mode(self.connection.db_model.iid, AIMode.disabled))


class AIModeSelectCallback(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (AIModeCallback.filter(),)

    async def handle(self) -> Any:
        await self.check_for_message()
        if not isinstance(self.event.message, Message):
            return await self.event.answer(_("Invalid message type"))

        message = self.event.message
        if not await is_user_admin(message.chat.id, self.event.from_user.id):
            return await self.event.answer(_("You are not allowed to change this setting"))

        mode = AIMode(self.callback_data.mode)
        if mode not in SELECTABLE_MODES:
            return await self.event.answer(_("Unknown mode"))

        # Re-selecting the current mode would rebuild a byte-identical keyboard, which Telegram
        # rejects with "message is not modified"; nothing changed, so only acknowledge the tap.
        current_mode: AIMode = await get_chat_mode(self.connection.db_model.iid, AIMode.disabled)
        if mode == current_mode:
            return await self.event.answer(str(MODE_TITLES[mode]))

        await set_chat_mode(self.connection.db_model, mode)

        # The picker may have been sent as a rich message, which cannot be edited; only the keyboard
        # is refreshed so the checkmark follows the selection.
        await message.edit_reply_markup(reply_markup=_build_keyboard(mode))
        return await self.event.answer(str(MODE_TITLES[mode]))
