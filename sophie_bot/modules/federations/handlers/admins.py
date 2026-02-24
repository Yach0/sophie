from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Bold, Code, Doc, HList, KeyValue, Section, Template, Title, UserLink, VList

from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("List all admins of a federation"))
@flags.disableable(name="fadmins")
class FederationAdminsHandler(FederationCommandHandler):
    """Handler for listing federation admins."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("fadmins", "fedadmins")), FeatureFlagFilter("new_feds_fadmins")

    async def handle_federation_command(self, federation: Federation) -> Any:
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        requester_tid = self.event.from_user.id
        if not await FederationPermissionService.can_ban_in_federation(federation, requester_tid):
            await self.event.reply(_("You don't have permission to view this federation's admins."))
            return

        owner = await federation.creator.fetch()
        owner_value = Bold(UserLink(owner.tid, owner.first_name_or_title)) if owner else _("Unknown")

        admin_models: list[ChatModel] = []
        for admin_link in federation.admins:
            admin_model = await admin_link.fetch()
            if not admin_model:
                continue
            if owner and admin_model.iid == owner.iid:
                continue
            admin_models.append(admin_model)

        admin_rows = VList(
            *(
                Template(
                    _("{number}. {admin}"),
                    number=str(admin_number),
                    admin=Bold(UserLink(admin_model.tid, admin_model.first_name_or_title)),
                )
                for admin_number, admin_model in enumerate(
                    sorted(admin_models, key=lambda admin: admin.first_name_or_title or ""),
                    start=1,
                )
            )
        )

        total_admins = len(admin_models) + (1 if owner else 0)
        doc = Doc(
            Title(_("🏛 Federation Admins")),
            KeyValue(_("Federation"), HList(federation.fed_name, Code(federation.fed_id))),
            KeyValue(_("Total admins"), str(total_admins)),
            KeyValue(_("Owner"), owner_value),
            Section(_("Admins"), admin_rows if admin_models else _("No additional admins.")),
        )
        await self.event.reply(doc.to_html())
