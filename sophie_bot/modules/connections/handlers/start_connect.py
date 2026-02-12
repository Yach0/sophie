from __future__ import annotations

from re import search
from typing import Any

from aiogram import F, flags
from aiogram.filters import CommandStart

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.connections.utils.connection import (
    check_connection_permissions,
    get_connection_text,
    get_disconnect_markup,
    set_connected_chat,
)
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


@flags.help(exclude=True)
class StartConnectHandler(SophieMessageHandler):
    @staticmethod
    def filters():
        return (CommandStart(deep_link=True, magic=F.args.regexp(r"connect_(-?\d+)")),)

    async def handle(self) -> Any:
        if not self.event.from_user or not self.event.text:
            return

        regex = search(r"connect_(-?\d+)", self.event.text)
        if not regex:
            return

        chat_tid = int(regex.group(1))
        user_tid = self.event.from_user.id

        chat_db = await ChatModel.get_by_tid(chat_tid)
        user_db = await ChatModel.get_by_tid(user_tid)

        if not chat_db or not user_db:
            return

        # Check permissions
        if not await check_connection_permissions(chat_db.iid, user_db.iid):
            return await self.event.reply(_("You are not allowed to connect to this chat."))

        await set_connected_chat(user_tid, chat_tid)
        text = await get_connection_text(chat_tid)
        markup = get_disconnect_markup()

        await self.event.reply(str(text), reply_markup=markup)
