from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg
from stfu_tg import Code, Doc, KeyValue, Template, Title, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services.federation import FederationService
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Check federation bans for a user in current chat"))
@flags.disableable(name="fcheck")
class FederationCheckGroupHandler(FederationCommandHandler):
    """Handler for checking fed bans in group or connected PM context."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("fcheck", "fbanstat")),
            FeatureFlagFilter("new_feds_fcheck"),
            GroupOrConnectedFilter(allow_abort=False),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        base_args = await super().handler_args(message, data)
        base_args.update({"user": OptionalArg(SophieUserArg(l_("User to check"), allow_unknown_id=True))})
        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        target_user: ChatModel | None = self.data.get("user")
        if not target_user:
            reply_message = self.event.reply_to_message
            reply_from_user = reply_message.from_user if reply_message else None
            if reply_from_user:
                target_user = await ChatModel.get_by_tid(reply_from_user.id)
                if not target_user:
                    raise ValueError("Target user not found in database")
            else:
                target_user = self.data.get("user_db")

        if not target_user:
            await self.event.reply(_("Please specify a user or reply to a user's message."))
            return

        user_tid = target_user.tid

        ban_in_fed = await FederationService.is_user_banned_in_chain(federation.fed_id, user_tid)
        fed_ban_count = await FederationService.count_user_fed_bans(user_tid)

        doc = Doc(
            Title(_("🏛 Federation Ban Check")),
            KeyValue(
                _("Federation"),
                Template("{fed_name} ({fed_id})", fed_name=federation.fed_name, fed_id=Code(federation.fed_id)),
            ),
            KeyValue(_("User"), UserLink(target_user.tid, target_user.first_name_or_title or _("Unknown"))),
        )

        doc += KeyValue(_("Total federation bans"), Code(str(fed_ban_count)))

        if ban_in_fed:
            ban, ban_fed = ban_in_fed
            reason = ban.reason or _("No reason provided")
            doc += KeyValue(
                _("Banned in current fed"),
                Template("{fed_name} ({fed_id})", fed_name=ban_fed.fed_name, fed_id=ban_fed.fed_id),
            )
            doc += KeyValue(_("Reason"), reason)
        else:
            doc += _("The user is not banned in the current federation.")

        if target_user.tid == self.event.from_user.id:
            doc += _("To see the full list of your federation bans, run /fcheck in Sophie's DM.")

        await self.event.reply(doc.to_html())
