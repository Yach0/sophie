from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import BlockQuote, Code, Doc, KeyValue, Section, Spacer, Template, Title, VList

from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.filters.utils_.filter_action_text import filter_action_text
from sophie_bot.modules.locks.handlers.lockable import get_lock_description, get_lock_display_name
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
        filter_lock_types = await get_filter_lock_types(connection.db_model.iid)

        if not locked_types and not filter_lock_types:
            doc = Doc(
                Template(_("No locks in {chat}"), chat=connection.title),
                Template(_("Use {cmd} to add a lock."), cmd=Code("/lock <type>")),
            )
            await message.reply(doc.to_html())
            return

        sorted_locks = sorted(locked_types, key=lambda x: (is_stickerpack_lock(x), x))
        sorted_filter_locks = sorted(filter_lock_types, key=lambda x: (is_stickerpack_lock(x.handler), x.handler))
        lock_names = [get_lock_display_name(lock_type) for lock_type in sorted_locks]
        filter_lock_names = [
            KeyValue(
                Code(filter_item.handler),
                Section(
                    filter_action_text(filter_item.action, list(filter_item.actions.keys())),
                    title=get_lock_description(filter_item.handler),
                    title_postfix=" -> ",
                    title_underline=False,
                    indent=2,
                ),
            )
            for filter_item in sorted_filter_locks
        ]

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
        doc += Template(_("Use {cmd} to see all available lock types."), cmd=Code("/lockable"))
        doc += Template(
            _("Use {lock_cmd} to add a lock or {filter_cmd} to add a filter lock."),
            lock_cmd=Code("/lock <type>"),
            filter_cmd=Code("/addfilter <type>"),
        )
        doc += Template(_("Use {cmd} to remove a lock."), cmd=Code("/unlock <type>"))
        await message.reply(doc.to_html())
