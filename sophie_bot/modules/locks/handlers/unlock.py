from __future__ import annotations

from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types import OptionalArg

from sophie_bot.args.lock_type import LockTypeArg
from sophie_bot.db.models import LocksModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.locks.handlers.base import BaseLockToggleHandler
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(lock_type=OptionalArg(LockTypeArg(l_("Lock type"))))
@flags.help(description=l_("Unlock a message type in the chat"))
@flags.disableable(name="unlock")
class UnlockHandler(BaseLockToggleHandler):
    usage_template: ClassVar[str | LazyProxy] = l_("Usage: {cmd}")
    usage_hint: ClassVar[str | LazyProxy] = l_("Use /locks to see currently locked types.")
    filters_module_text: ClassVar[str | LazyProxy] = l_(
        "Lock type {type} is enforced by the Filters module, not Locks."
    )
    filters_module_delete_hint: ClassVar[str | LazyProxy] = l_(
        "Delete it there with {cmd} if you want to stop enforcing it."
    )
    success_title: ClassVar[str | LazyProxy] = l_("Lock removed")
    already_state_text: ClassVar[str | LazyProxy] = l_("Lock type {type} is not locked in this chat.")

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("unlock"),
            UserRestricting(admin=True),
            FeatureFlagFilter("locks"),
        )

    async def _toggle_lock(self, model: LocksModel, lock_type: str) -> bool:
        return await model.unlock(lock_type)

    @property
    def _command_name(self) -> str:
        return "unlock"
