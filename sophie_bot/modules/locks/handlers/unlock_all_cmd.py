from __future__ import annotations

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from stfu_tg import Italic, Template

from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.locks.callbacks import UnlockAllCallback
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class UnlockAllCmdHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("unlockall"),
            UserRestricting(admin=True),
            FeatureFlagFilter("locks"),
        )

    async def handle(self) -> None:
        connection = self.connection

        if not self.event.from_user:
            return

        model = await LocksModel.get_by_chat_iid(connection.db_model.iid)
        locked_types = model.locked_types

        if not locked_types:
            await self.event.reply(
                Template(
                    _(str(l_("There are no locked types in {chat_name}."))),
                    chat_name=Italic(connection.title),
                ).to_html()
            )
            return

        buttons = InlineKeyboardBuilder()
        buttons.add(
            InlineKeyboardButton(
                text=_(str(l_("✅ Unlock all"))),
                callback_data=UnlockAllCallback(user_id=self.event.from_user.id).pack(),
            ),
        )
        buttons.add(
            InlineKeyboardButton(
                text=_(str(l_("🚫 Cancel"))),
                callback_data="cancel",
            ),
        )

        await self.event.reply(
            text=str(
                Template(
                    _(str(l_("Do you want to unlock all {count} lock types in {chat_name}?"))),
                    count=len(locked_types),
                    chat_name=Italic(connection.title),
                )
            ),
            reply_markup=buttons.as_markup(),
        )
