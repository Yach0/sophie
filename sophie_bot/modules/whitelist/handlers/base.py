from __future__ import annotations

from abc import ABC
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types.base_abc import ArgFabric

from sophie_bot.args.users import SophieUserArg
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.utils_.get_user import UnionUser, get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import lazy_gettext as l_


def whitelist_mutation_filters(commands: tuple[str, ...]) -> tuple[CallbackType, ...]:
    return (
        CMDFilter(commands),
        FeatureFlagFilter("group_user_whitelist"),
        ChatTypeFilter("group", "supergroup"),
        UserRestricting(can_restrict_members=True),
    )


class BaseWhitelistMutationHandler(SophieMessageHandler, ABC):
    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, Any]) -> dict[str, ArgFabric]:
        if message and is_real_reply(message):
            return {}
        return {"user": SophieUserArg(l_("User"))}

    def target(self) -> UnionUser:
        return get_union_user(get_arg_or_reply_user(self.event, self.data))
