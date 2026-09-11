from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import KeyValue, Section, Template, UserLink

from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.modules.whitelist.handlers.base import BaseWhitelistMutationHandler, whitelist_mutation_filters
from sophie_bot.utils import flags
from sophie_bot.utils.group_whitelist import remove_user_from_group_whitelist
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Remove a user from the group whitelist for automated moderation."))
class UnwhitelistUserHandler(BaseWhitelistMutationHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return whitelist_mutation_filters(("unwhitelist", "untrust"))

    async def handle(self) -> Any:
        user = self.target()
        chat_tid = self.event.chat.id
        removed = await remove_user_from_group_whitelist(chat_tid, user.chat_id, redis=self.services.redis)

        status = (
            _("The user is no longer whitelisted in this group.")
            if removed
            else _("The user was not whitelisted in this group.")
        )
        document = Section(
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            status,
            Template(_("Automated moderation now applies normally; existing restrictions are unchanged.")),
            title=_("Group whitelist updated"),
        )
        await reply_or_answer(self.event, document)
