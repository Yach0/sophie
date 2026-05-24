from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from aiogram.types import Message
from stfu_tg import Code, Doc, KeyValue, Section, Template

from sophie_bot.db.models import LocksModel
from sophie_bot.modules.locks.utils.cache import invalidate_locks_cache
from sophie_bot.modules.locks.utils.conflicts import get_lock_type_owner
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


class BaseLockToggleHandler(SophieMessageHandler):
    usage_template: ClassVar[str]
    usage_hint: ClassVar[str]
    filters_module_text: ClassVar[str]
    filters_module_delete_hint: ClassVar[str]
    success_title: ClassVar[str]
    already_state_text: ClassVar[str]

    async def handle(self) -> Any:
        message: Message = self.event
        connection = self.connection
        lock_type: str | None = self.data.get("lock_type")

        if not lock_type:
            doc = Doc(
                Template(str(self.usage_template), cmd=Code(f"/{self._command_name} <lock_type>")),
                str(self.usage_hint),
            )
            await message.reply(doc.to_html())
            return

        lock_type = lock_type.lower()

        existing_owner = await get_lock_type_owner(connection.db_model.iid, lock_type)
        if existing_owner == "filters":
            await message.reply(
                Doc(
                    Template(str(self.filters_module_text), type=lock_type),
                    Template(str(self.filters_module_delete_hint), cmd=f"/delfilter {lock_type}"),
                ).to_html()
            )
            return

        model = await LocksModel.get_by_chat_iid(connection.db_model.iid)
        changed = await self._toggle_lock(model, lock_type)

        await invalidate_locks_cache(connection.tid)

        if changed:
            doc = Doc(
                Section(
                    KeyValue(_("Chat"), connection.title),
                    KeyValue(_("Lock type"), lock_type),
                    title=str(self.success_title),
                )
            )
        else:
            doc = Doc(Template(str(self.already_state_text), type=lock_type))

        await message.reply(doc.to_html())

    @abstractmethod
    async def _toggle_lock(self, model: LocksModel, lock_type: str) -> bool:
        raise NotImplementedError

    @property
    def _command_name(self) -> str:
        raise NotImplementedError
