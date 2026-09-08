from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.whitelist.callbacks import WhitelistPageCallback, WhitelistRemoveCallback
from sophie_bot.modules.whitelist.handlers.list import get_group_whitelist_entries, render_whitelist_page
from sophie_bot.utils.group_whitelist import remove_user_from_group_whitelist
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class WhitelistPageHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WhitelistPageCallback.filter(),
            FeatureFlagFilter("group_user_whitelist"),
            ChatTypeFilter("group", "supergroup"),
        )

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        await callback.answer()
        chat_tid = self.connection.tid
        entries = await get_group_whitelist_entries(chat_tid)
        await render_whitelist_page(callback, entries, self.callback_data.page, bot=self.services.bot)


class WhitelistRemoveHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WhitelistRemoveCallback.filter(),
            FeatureFlagFilter("group_user_whitelist"),
            ChatTypeFilter("group", "supergroup"),
            UserRestricting(can_restrict_members=True),
        )

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        chat_tid = self.connection.tid
        removed = await remove_user_from_group_whitelist(
            chat_tid,
            self.callback_data.user_tid,
            redis=self.services.redis,
        )
        await callback.answer(
            _("User removed from this group's whitelist.")
            if removed
            else _("The user was not whitelisted in this group.")
        )
        entries = await get_group_whitelist_entries(chat_tid)
        await render_whitelist_page(callback, entries, self.callback_data.page, bot=self.services.bot)
