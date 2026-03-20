from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import BlockQuote, Doc, KeyValue, Section, Spacer, Template, Title, VList

from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.locks.handlers.lockable import get_lock_display_name
from sophie_bot.modules.locks.utils.conflicts import get_filter_lock_types
from sophie_bot.modules.locks.utils.lock_types import is_stickerpack_lock
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Show currently locked message types in the chat"))
@flags.disableable(name="locks")
class LocksListHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("locks", "locked")),
            UserRestricting(admin=True),
            FeatureFlagFilter("locks"),
        )

    async def handle(self) -> Any:
        message: Message = self.event
        connection = self.connection

        model = await LocksModel.get_by_chat_iid(connection.db_model.iid)
        locked_types = model.locked_types
        filter_lock_types = [
            filter_item.handler for filter_item in await get_filter_lock_types(connection.db_model.iid)
        ]

        if not locked_types and not filter_lock_types:
            doc = Doc(
                Template(_("No locks in {chat}"), chat=connection.title),
                _("Use /lock <type> to add a lock."),
            )
            await message.reply(doc.to_html())
            return

        sorted_locks = sorted(locked_types, key=lambda x: (is_stickerpack_lock(x), x))
        sorted_filter_locks = sorted(filter_lock_types, key=lambda x: (is_stickerpack_lock(x), x))
        lock_names = [get_lock_display_name(lock_type) for lock_type in sorted_locks]
        filter_lock_names = [get_lock_display_name(lock_type) for lock_type in sorted_filter_locks]

        doc = Doc(
            Title(_("Active locks")),
            KeyValue(_("Chat"), connection.title),
            BlockQuote(VList(*lock_names), expandable=True)
            if lock_names
            else Template(_("No rules from Locks module.")),
        )

        if filter_lock_names:
            doc += BlockQuote(
                Section(VList(*filter_lock_names), title=_("Filter-enforced lock types")), expandable=True
            )

        doc += Spacer()
        doc += Template(item=_("Use /lock <type> to add a lock or /addfilter <type> to add a filter lock."))
        doc += Template(item=_("Use /unlock <type> to remove a lock."))
        await message.reply(doc.to_html())
