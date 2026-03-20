from __future__ import annotations
from typing import Any
from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import Doc, KeyValue, Section, Template
from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.locks.utils.cache import invalidate_locks_cache
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Lock a sticker pack in the chat"))
@flags.disableable(name="locksticker")
class LockStickerHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("locksticker"),
            UserRestricting(admin=True),
            FeatureFlagFilter("locks"),
        )

    async def handle(self) -> Any:
        message: Message = self.event
        connection = self.connection
        reply_to_message = message.reply_to_message
        if not reply_to_message:
            doc = Doc(Template(_("Reply to a sticker to lock its sticker pack.")))
            await message.reply(doc.to_html())
            return
        sticker = reply_to_message.sticker
        if not sticker:
            doc = Doc(
                Template(
                    _("Reply to a message with a sticker to use {cmd} to lock a sticker pack."), cmd="/locksticker"
                ),
            )
            await message.reply(doc.to_html())
            return
        pack_name = sticker.set_name
        if not pack_name:
            doc = Doc(
                Section(
                    KeyValue(_("Chat"), connection.title),
                    KeyValue(_("Sticker pack"), _("Unknown")),
                    title=_("Sticker pack locked"),
                )
            )
            await message.reply(doc.to_html())
            return
        lock_type = f"stickerpack:{pack_name}"
        model = await LocksModel.get_by_chat_iid(connection.db_model.iid)
        added = await model.lock(lock_type)
        await invalidate_locks_cache(connection.tid)
        if added:
            doc = Doc(
                Section(
                    KeyValue(_("Chat"), connection.title),
                    KeyValue(_("Sticker pack"), pack_name),
                    title=_("Sticker pack locked"),
                )
            )
        else:
            doc = Doc(Template(_("Sticker pack {pack} is already locked in this chat."), pack=pack_name))
        await message.reply(doc.to_html())
