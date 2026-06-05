from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, Italic, KeyValue, Section, Template

from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.modules.locks.callbacks import UnlockAllCallback
from sophie_bot.modules.locks.utils.cache import invalidate_locks_cache
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class UnlockAllCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return UnlockAllCallback.filter(), UserRestricting(admin=True)

    async def handle(self) -> Any:
        connection = self.connection
        data: UnlockAllCallback = self.callback_data

        if self.event.from_user.id != data.user_id:
            await self.event.answer(_(str(l_("Only the initiator can confirm this action."))))
            return

        model = await LocksModel.get_by_chat_iid(connection.db_model.iid)
        locked_types = model.locked_types

        if not locked_types:
            await self.edit_text(
                Template(
                    _(str(l_("There are no locked types in {chat_name}."))),
                    chat_name=Italic(connection.title),
                ),
            )
            return

        removed_count = await model.unlock_multiple(locked_types)
        await invalidate_locks_cache(connection.tid)

        doc = Doc(
            Section(
                KeyValue(_(str(l_("Chat"))), connection.title),
                KeyValue(_(str(l_("Unlocked"))), removed_count),
                title=_(str(l_("All locks removed"))),
            )
        )

        await self.edit_text(doc)
