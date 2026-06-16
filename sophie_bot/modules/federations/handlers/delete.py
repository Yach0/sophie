from __future__ import annotations

from typing import Any, cast

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from stfu_tg import Doc, KeyValue, Template, Title, UserLink

from sophie_bot.db.models import Federation
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class FederationDeleteCallback(CallbackData, prefix="fdelete"):
    fed_id: str


def build_delete_confirmation_keyboard(fed_id: str) -> InlineKeyboardMarkup:
    """Build confirmation keyboard for federation deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=str(l_("✅ Confirm")),
                    callback_data=FederationDeleteCallback(fed_id=fed_id).pack(),
                ),
                InlineKeyboardButton(
                    text=str(l_("❌ Cancel")),
                    callback_data="cancel",
                ),
            ]
        ]
    )


@flags.help(description=l_("Delete a federation (owner only)"))
class FederationDeleteHandler(FederationCommandHandler):
    """Handler for requesting federation deletion."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("fdelete",)),)

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Ask the owner to confirm federation deletion."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        if not await self.require_owner(federation):
            return

        await self.event.reply(
            Doc(
                Title(_("🏛 Delete Federation?")),
                Template(
                    _("Are you sure you want to delete federation '{name}'?"),
                    name=federation.fed_name,
                ),
                KeyValue(_("Federation ID"), federation.fed_id),
                _("This will delete the federation and all federation bans. This action cannot be undone."),
            ).to_html(),
            reply_markup=build_delete_confirmation_keyboard(federation.fed_id),
        )


class FederationDeleteCallbackHandler(SophieCallbackQueryHandler):
    """Handle federation deletion confirmation callbacks."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (FederationDeleteCallback.filter(),)

    async def handle(self) -> Any:
        """Confirm federation deletion."""
        await self.check_for_message()
        data = cast(FederationDeleteCallback, self.callback_data)

        if not isinstance(self.event.message, Message):
            await self.event.answer(_("Invalid message type"))
            return

        federation = await FederationManageService.get_federation_by_id(data.fed_id)
        if not federation:
            await self.event.message.edit_text(text=_("This federation no longer exists."))
            await self.event.answer(_("Federation not found"))
            return

        if not await FederationPermissionService.is_federation_owner(federation, self.event.from_user.id):
            await self.event.answer(_("Only federation owners can perform this action."), show_alert=True)
            return

        fed_name = federation.fed_name
        fed_id = federation.fed_id

        log_text = Template(
            _("🏛 Federation '{name}' has been deleted by {user}."),
            name=fed_name,
            user=UserLink(self.event.from_user.id, self.event.from_user.first_name),
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)

        await FederationManageService.delete_federation(federation)

        await self.event.message.edit_text(
            text=Doc(
                Title(_("🏛 Federation Deleted")),
                Template(_("Federation '{name}' has been deleted."), name=fed_name),
                KeyValue(_("Federation ID"), fed_id),
            ).to_html()
        )
        await self.event.answer(_("Federation deleted"))
