from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg
from stfu_tg import Code, Doc, KeyValue, Section, Template

from sophie_bot.args.lock_type import LockTypeArg
from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.locks.utils.cache import invalidate_locks_cache
from sophie_bot.modules.locks.utils.conflicts import get_lock_type_owner
from sophie_bot.modules.locks.utils.lock_types import ALL_LOCK_TYPES, is_language_lock, is_stickerpack_lock
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(lock_type=OptionalArg(LockTypeArg(l_("Lock type"))))
@flags.help(description=l_("Unlock a message type in the chat"))
@flags.disableable(name="unlock")
class UnlockHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("unlock"),
            UserRestricting(admin=True),
            FeatureFlagFilter("locks"),
        )

    async def handle(self) -> Any:
        message: Message = self.event
        connection = self.connection
        lock_type: str | None = self.data.get("lock_type")

        if not lock_type:
            doc = Doc(
                Template(_("Usage: {cmd}"), cmd=Code("/unlock <lock_type>")),
                _("Use /locks to see currently locked types."),
            )
            await message.reply(doc.to_html())
            return

        lock_type = lock_type.lower()

        is_valid = lock_type in ALL_LOCK_TYPES or is_stickerpack_lock(lock_type) or is_language_lock(lock_type)

        if not is_valid:
            await message.reply(Template(_("Unknown lock type: {type}"), type=lock_type).to_html())
            return

        existing_owner = await get_lock_type_owner(connection.db_model.iid, lock_type)
        if existing_owner == "filters":
            await message.reply(
                Doc(
                    Template(
                        _("Lock type {type} is enforced by the Filters module, not Locks."),
                        type=lock_type,
                    ),
                    Template(
                        _("Delete it there with {cmd} if you want to stop enforcing it."),
                        cmd=f"/delfilter {lock_type}",
                    ),
                ).to_html()
            )
            return

        model = await LocksModel.get_by_chat_iid(connection.db_model.iid)
        removed = await model.unlock(lock_type)

        await invalidate_locks_cache(connection.tid)

        if removed:
            doc = Doc(
                Section(
                    KeyValue(_("Chat"), connection.title),
                    KeyValue(_("Lock type"), lock_type),
                    title=_("Lock removed"),
                )
            )
        else:
            doc = Doc(Template(_("Lock type {type} is not locked in this chat."), type=lock_type))

        await message.reply(doc.to_html())
