from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage, Message
from stfu_tg import Doc, Italic, RichTable, RichTableCell, Title

from sophie_bot.constants import AI_EMOJI
from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.callbacks import AIModeratorCategoryCallback
from sophie_bot.modules.ai.utils.moderation.categories import (
    MODERATION_CATEGORIES_TITLES,
    MODERATION_CATEGORIES_TRANSLATES,
    ModerationCategory,
)
from sophie_bot.modules.ai.utils.moderation.settings import (
    get_levels,
    get_moderator_settings,
    next_level,
    set_category_level,
)
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

LEVEL_TITLES: Mapping[DetectionLevel, LazyProxy] = {
    DetectionLevel.OFF: l_("🚫 Off"),
    DetectionLevel.LOW: l_("🟢 Low"),
    DetectionLevel.NORMAL: l_("🟡 Medium"),
    DetectionLevel.HIGH: l_("🔴 High"),
}

_BUTTONS_PER_ROW: Final[int] = 2


def _build_table() -> RichTable:
    return RichTable(
        [RichTableCell(_("Category"), is_header=True), RichTableCell(_("What it detects"), is_header=True)],
        *(
            [
                RichTableCell(str(MODERATION_CATEGORIES_TITLES[category])),
                RichTableCell(str(MODERATION_CATEGORIES_TRANSLATES[category])),
            ]
            for category in ModerationCategory
        ),
        bordered=True,
    )


def _build_doc() -> Doc:
    return Doc(
        Title(f"{AI_EMOJI} {_('AI Moderator')}"),
        _("Sophie deletes messages that cross a category's detection level."),
        _build_table(),
        Italic(_("Press a category to walk it through Off, Low, Medium and High.")),
    )


def _build_keyboard(levels: Mapping[ModerationCategory, DetectionLevel]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{MODERATION_CATEGORIES_TITLES[category]}: {LEVEL_TITLES[levels[category]]}",
            callback_data=AIModeratorCategoryCallback(category=category.value).pack(),
        )
        for category in ModerationCategory
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[index : index + _BUTTONS_PER_ROW] for index in range(0, len(buttons), _BUTTONS_PER_ROW)
        ]
    )


async def _send_picker(message: Message, settings: AIModeratorModel | None) -> None:
    doc = _build_doc()
    keyboard = _build_keyboard(get_levels(settings))
    try:
        await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            rich_message=InputRichMessage(html=doc.to_rich()),
            reply_markup=keyboard,
        )
    except TelegramAPIError:
        await message.reply(doc.to_html(), reply_markup=keyboard)


@flags.help(description=l_("Tune what the AI moderator detects in this chat"))
class AIModeratorSetting(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("aimoderator"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        await _send_picker(self.event, await get_moderator_settings(self.connection.db_model.iid))


class AIModeratorCategoryToggle(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (AIModeratorCategoryCallback.filter(),)

    async def handle(self) -> Any:
        await self.check_for_message()
        if not isinstance(self.event.message, Message):
            return await self.event.answer(_("Invalid message type"))

        message = self.event.message
        if not await is_user_admin(message.chat.id, self.event.from_user.id):
            return await self.event.answer(_("You are not allowed to change this setting"))

        try:
            category = ModerationCategory(self.callback_data.category)
        except ValueError:
            return await self.event.answer(_("Unknown category"))

        chat = self.connection.db_model
        levels = get_levels(await get_moderator_settings(chat.iid))
        level = next_level(levels[category])
        levels = {**levels, category: level}
        await set_category_level(chat, category, level)

        # The picker may have been sent as a rich message, which cannot be edited; only the keyboard
        # is refreshed, which is also where every level is displayed.
        await message.edit_reply_markup(reply_markup=_build_keyboard(levels))
        return await self.event.answer(f"{MODERATION_CATEGORIES_TITLES[category]}: {LEVEL_TITLES[level]}")
