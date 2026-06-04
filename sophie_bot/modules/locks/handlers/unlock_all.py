from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, KeyValue, Section, Template

from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.modules.locks.callbacks import UnlockAllCallback
from sophie_bot.modules.locks.utils.cache import invalidate_locks_cache
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class UnlockAllCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return UnlockAllCallback.filter(), UserRestricting(admin=True)

    async def handle(self) -> Any:
        await self.check_for_message()

        connection = self.connection
        data: UnlockAllCallback = self.callback_data

        if self.event.from_user.id != data.user_id:
            await self.event.answer(_("Only the initiator can confirm unlocking all types."))
            return

        model = await LocksModel.get_by_chat_iid(connection.db_model.iid)
        locked_types = model.locked_types

        if not locked_types:
            await self.event.message.edit_text(
                Template(
                    _("There are no locked types in {chat_name}."),
                    chat_name=connection.title,
                ).to_html(),
            )
            return

        removed_count = await model.unlock_multiple(locked_types)
        await invalidate_locks_cache(connection.tid)

        doc = Doc(
            Section(
                KeyValue(_("Chat"), connection.title),
                KeyValue(_("Unlocked"), removed_count),
                title=_("All locks removed"),
            )
        )

        await self.event.message.edit_text(doc.to_html())
