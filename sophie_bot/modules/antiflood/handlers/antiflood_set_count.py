from __future__ import annotations

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType

from ass_tg.types import IntArg, OptionalArg

from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Set the message count threshold for antiflood protection"))
class AntifloodSetCountHandler(SophieMessageHandler):
    """Handler for setting antiflood message count threshold."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("antiflood_count",)),
            UserRestricting(admin=True),
            FeatureFlagFilter("antiflood"),
        )

    @classmethod
    async def handler_args(cls, message, data):
        return {
            "count": OptionalArg(IntArg(l_("Number of messages allowed in 30 seconds"))),
        }

    async def handle(self):
        count = self.data.get("count")
        chat = self.connection.db_model

        if not chat:
            await self.event.reply(_("Chat not found"))
            return

        if count is None:
            await self.event.reply(_("Please provide a number. Usage: /antiflood_count <number>"))
            return

        if count < 1 or count > 100:
            await self.event.reply(_("Number must be between 1 and 100"))
            return

        model = await AntifloodModel.find_one(AntifloodModel.chat.id == chat.iid)
        if not model:
            model = AntifloodModel(chat=chat, enabled=True, message_count=count)
            await model.save()
        else:
            model.message_count = count
            await model.save()

        await self.event.reply(_("✅ Antiflood threshold updated to {count} messages per 30 seconds", count=count))
