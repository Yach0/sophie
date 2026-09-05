from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import KeyValue, Section, Template, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.models.global_user_whitelist import GlobalUserWhitelistModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.get_user import UnionUser, get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class BaseWhitelistHandler(SophieMessageHandler):
    command: str

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, Any]) -> dict[str, ArgFabric]:
        if message and is_real_reply(message):
            return {}
        return {"user": SophieUserArg(l_("User"))}

    @classmethod
    def filters(cls) -> tuple[CallbackType, ...]:
        return (
            CMDFilter(cls.command),
            ChatTypeFilter("group", "supergroup"),
            UserRestricting(can_restrict_members=True),
        )

    def target(self) -> UnionUser:
        return get_union_user(get_arg_or_reply_user(self.event, self.data))


@flags.help(description=l_("Globally exempt a user from automated moderation."))
class WhitelistUserHandler(BaseWhitelistHandler):
    command = "whitelist"

    async def handle(self) -> Any:
        user = self.target()
        added = await GlobalUserWhitelistModel.add_user(user.chat_id)

        if added:
            status = _("The user is now globally whitelisted.")
        else:
            status = _("The user is already globally whitelisted.")

        doc = Section(
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            status,
            Template(
                _("Automated moderation will be skipped in every chat. This does not grant administrator rights.")
            ),
            title=_("Global whitelist updated"),
        )
        await reply_or_answer(self.event, doc)


@flags.help(description=l_("Remove a user's global automated-moderation exemption."))
class UnwhitelistUserHandler(BaseWhitelistHandler):
    command = "unwhitelist"

    async def handle(self) -> Any:
        user = self.target()
        removed = await GlobalUserWhitelistModel.remove_user(user.chat_id)

        if removed:
            status = _("The user is no longer globally whitelisted.")
        else:
            status = _("The user was not globally whitelisted.")

        doc = Section(
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            status,
            Template(_("Automated moderation now applies normally; existing restrictions are unchanged.")),
            title=_("Global whitelist updated"),
        )
        await reply_or_answer(self.event, doc)
