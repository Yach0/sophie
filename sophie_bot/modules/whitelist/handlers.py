from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import KeyValue, Section, Template, UserLink, VList

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.models import ChatModel, GroupUserWhitelistModel, WSUserModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.restrictions.utils.restrictions import unmute_user
from sophie_bot.modules.utils_.get_user import UnionUser, get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.utils import flags
from sophie_bot.utils.group_whitelist import add_user_to_group_whitelist, remove_user_from_group_whitelist
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

WHITELIST_LIST_LIMIT = 50


class BaseWhitelistHandler(SophieMessageHandler):
    commands: tuple[str, ...]

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, Any]) -> dict[str, ArgFabric]:
        if message and is_real_reply(message):
            return {}
        return {"user": SophieUserArg(l_("User"))}

    @classmethod
    def filters(cls) -> tuple[CallbackType, ...]:
        return (
            CMDFilter(cls.commands),
            FeatureFlagFilter("group_user_whitelist"),
            ChatTypeFilter("group", "supergroup"),
            UserRestricting(can_restrict_members=True),
        )

    def target(self) -> UnionUser:
        return get_union_user(get_arg_or_reply_user(self.event, self.data))


@flags.help(description=l_("Add a user to the group whitelist for automated moderation."))
class WhitelistUserHandler(BaseWhitelistHandler):
    commands = ("whitelist", "trust")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, Any]) -> dict[str, ArgFabric]:
        del cls, data
        if message and is_real_reply(message):
            return {}
        return {"user": OptionalArg(SophieUserArg(l_("User")))}

    async def handle(self) -> Any:
        if not (is_real_reply(self.event) or self.data.get("user")):
            return await self._list_users()

        user = self.target()
        chat_tid = self.event.chat.id
        added = await add_user_to_group_whitelist(chat_tid, user.chat_id, redis=self.services.redis)
        await self._release_pending_captcha_user(chat_tid, user.chat_id)

        if added:
            status = _("The user is now whitelisted in this group.")
        else:
            status = _("The user is already whitelisted in this group.")

        doc = Section(
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            status,
            Template(_("Automated moderation will be skipped only in this group.")),
            title=_("Group whitelist updated"),
        )
        await reply_or_answer(self.event, doc)

    async def _list_users(self) -> Any:
        chat_tid = self.event.chat.id
        entries = (
            await GroupUserWhitelistModel.find(GroupUserWhitelistModel.chat_tid == chat_tid)
            .limit(WHITELIST_LIST_LIMIT + 1)
            .to_list()
        )
        if not entries:
            return await reply_or_answer(self.event, _("No users are whitelisted in this group."))

        visible_entries = entries[:WHITELIST_LIST_LIMIT]
        users = [await ChatModel.get_by_tid(entry.user_tid) for entry in visible_entries]
        user_rows = VList(
            *(
                UserLink(entry.user_tid, user.first_name_or_title if user else _("Unknown user"))
                for entry, user in zip(visible_entries, users, strict=True)
            )
        )
        doc = Section(
            user_rows,
            Template(
                _("Showing the first {limit} users."),
                limit=WHITELIST_LIST_LIMIT,
            )
            if len(entries) > WHITELIST_LIST_LIMIT
            else None,
            title=_("Users whitelisted in this group"),
        )
        return await reply_or_answer(self.event, doc)

    @staticmethod
    async def _release_pending_captcha_user(chat_tid: int, user_tid: int) -> None:
        group = await ChatModel.get_by_tid(chat_tid)
        user = await ChatModel.get_by_tid(user_tid)
        if group is None or user is None or await WSUserModel.is_user(user.iid, group.iid) is None:
            return

        await unmute_user(chat_tid=chat_tid, user_tid=user_tid)
        await WSUserModel.remove_user(user.iid, group.iid)


@flags.help(description=l_("Remove a user from the group whitelist for automated moderation."))
class UnwhitelistUserHandler(BaseWhitelistHandler):
    commands = ("unwhitelist", "untrust")

    async def handle(self) -> Any:
        user = self.target()
        chat_tid = self.event.chat.id
        removed = await remove_user_from_group_whitelist(chat_tid, user.chat_id, redis=self.services.redis)

        if removed:
            status = _("The user is no longer whitelisted in this group.")
        else:
            status = _("The user was not whitelisted in this group.")

        doc = Section(
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            status,
            Template(_("Automated moderation now applies normally; existing restrictions are unchanged.")),
            title=_("Group whitelist updated"),
        )
        await reply_or_answer(self.event, doc)
