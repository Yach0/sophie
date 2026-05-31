from __future__ import annotations

from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.federations.handlers.promote_demote_base import FederationPromoteDemoteHandler
from sophie_bot.modules.federations.services import FederationAdminService
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Demote a user from federation admin"))
class FederationDemoteHandler(FederationPromoteDemoteHandler):
    action_name: ClassVar[str | LazyProxy] = l_("User")
    owner_only_text: ClassVar[str | LazyProxy] = l_("Only the federation owner can demote users.")
    user_not_specified_text: ClassVar[str | LazyProxy] = l_(
        "Please specify a user to demote or reply to their message."
    )
    not_private_user_text: ClassVar[str | LazyProxy] = l_("Can only demote individual users from admin.")
    success_template: ClassVar[str | LazyProxy] = l_("{user} has been demoted from admin of federation {fed_name}.")
    log_template: ClassVar[str | LazyProxy] = l_("👤 {admin} demoted {user} from admin in federation {fed_name}.")

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("fdemote",)),)

    async def _execute_action(self, federation: Federation, user_db: ChatModel) -> None:
        creator = await federation.creator.fetch()
        if creator and creator.iid == user_db.iid:
            await self.event.reply(_("Cannot demote the federation owner."))
            return

        try:
            await FederationAdminService.demote_admin(federation, user_db.iid)
        except ValueError as e:
            if "not an admin" in str(e):
                from stfu_tg import Bold, Template, UserLink

                await self.event.reply(
                    Template(
                        _("{user} is not an admin of this federation."),
                        user=Bold(UserLink(user_db.tid, user_db.first_name_or_title)),
                    ).to_html()
                )
            else:
                await self.event.reply(str(e))
            return

        await self._send_success(federation, user_db)
        await self._log_action(federation, user_db)
