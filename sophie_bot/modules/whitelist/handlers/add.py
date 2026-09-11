from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import KeyValue, Section, Template, UserLink

from sophie_bot.db.models import ChatModel, WSUserModel
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.modules.whitelist.handlers.base import BaseWhitelistMutationHandler, whitelist_mutation_filters
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils import flags
from sophie_bot.utils.group_whitelist import add_user_to_group_whitelist
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.handler_help(description=l_("Add a user to the group whitelist for automated moderation."))
class WhitelistUserHandler(BaseWhitelistMutationHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return whitelist_mutation_filters(("whitelist", "trust"))

    async def handle(self) -> Any:
        user = self.target()
        chat_tid = self.event.chat.id
        added = await add_user_to_group_whitelist(chat_tid, user.chat_id, redis=self.services.redis)
        await _release_pending_captcha_user(chat_tid, user.chat_id, bot=self.services.bot)

        status = (
            _("The user is now whitelisted in this group.")
            if added
            else _("The user is already whitelisted in this group.")
        )
        document = Section(
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            status,
            Template(_("Automated moderation will be skipped only in this group.")),
            title=_("Group whitelist updated"),
        )
        await reply_or_answer(self.event, document)


async def _release_pending_captcha_user(chat_tid: int, user_tid: int, *, bot: Bot) -> None:
    group = await ChatModel.get_by_tid(chat_tid)
    user = await ChatModel.get_by_tid(user_tid)
    if group is None or user is None or await WSUserModel.is_user(user.iid, group.iid) is None:
        return

    result = await execute_restriction(bot, RestrictionAction.UNMUTE, chat_tid, user_tid)
    if result.applied:
        await WSUserModel.remove_user(user.iid, group.iid)
