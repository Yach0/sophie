from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from sophie_bot.modules.federations.args.fed_id import FedIdArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Doc, KeyValue, Title, Template

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.services.bot import bot
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Subscribe federation to another federation"))
class SubscribeFederationHandler(FederationCommandHandler):
    """Handler for subscribing federations to other federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("fsub",)), FeatureFlagFilter("new_feds_fsub")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"fed_id": FedIdArg(l_("Federation ID to subscribe to"))}

    async def handle(self) -> Any:
        """Subscribe federation to another federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        target_fed = self.data["fed_id"]
        target_fed_id = target_fed.fed_id

        # Get federation for current chat
        chat_iid = self.connection.db_model.iid
        federation = await FederationManageService.get_federation_for_chat(chat_iid)
        if not federation:
            await self.event.reply(_("This chat is not in a federation."))
            return

        if not await self.require_owner(federation):
            return

        # Subscribe to federation
        success = await FederationManageService.subscribe_to_federation(federation, target_fed_id)
        if not success:
            target_fed = await FederationManageService.get_federation_by_id(target_fed_id)
            if not target_fed:
                await self.event.reply(_("Federation not found."))
                return

            doc = Doc(
                Title(_("🏛 Subscription Failed")),
                Template(
                    _("Federation '{name}' is already subscribed to '{name2}'."),
                    name=federation.fed_name,
                    name2=target_fed.fed_name,
                ),
            )
            await self.event.reply(str(doc))
            return

        # Format response using STFU
        doc = Doc(
            Title(_("🏛 Federation Subscribed")),
            KeyValue(_("Federation"), federation.fed_name),
            KeyValue(_("Subscribed to"), target_fed.fed_name),
        )

        await self.event.reply(str(doc))

        # Log the subscription
        log_text = Template(
            _("🏛 Federation '{fed_name}' ({fed_id}) subscribed to '{target_fed_name}' ({target_fed_id})"),
            fed_name=federation.fed_name,
            fed_id=federation.fed_id,
            target_fed_name=target_fed.fed_name,
            target_fed_id=target_fed.fed_id,
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, bot)


@flags.help(description=l_("Unsubscribe federation from another federation"))
class UnsubscribeFederationHandler(FederationCommandHandler):
    """Handler for unsubscribing federations from other federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("funsub",)), FeatureFlagFilter("new_feds_funsub"))

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"fed_id": FedIdArg(l_("Federation ID to unsubscribe from"))}

    async def handle(self) -> Any:
        """Unsubscribe federation from another federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        target_fed = self.data["fed_id"]
        fed_id = target_fed.fed_id

        # Get federation for current chat
        chat_iid = self.connection.db_model.iid
        federation = await FederationManageService.get_federation_for_chat(chat_iid)
        if not federation:
            await self.event.reply(_("This chat is not in a federation."))
            return

        if not await self.require_owner(federation):
            return

        # Unsubscribe from federation
        success = await FederationManageService.unsubscribe_from_federation(federation, fed_id)
        if not success:
            target_fed = await FederationManageService.get_federation_by_id(fed_id)
            if not target_fed:
                await self.event.reply(_("Federation not found."))
                return

            doc = Doc(
                Title(_("🏛 Unsubscription Failed")),
                Template(
                    _("Federation '{name}' is not subscribed to '{name2}'."),
                    name=federation.fed_name,
                    name2=target_fed.fed_name,
                ),
            )
            await self.event.reply(str(doc))
            return

        # Format response using STFU
        doc = Doc(
            Title(_("🏛 Federation Unsubscribed")),
            KeyValue(_("Federation"), federation.fed_name),
            KeyValue(_("Unsubscribed from"), target_fed.fed_name),
        )

        await self.event.reply(str(doc))

        # Log the unsubscription
        log_text = Template(
            _("🏛 Federation '{fed_name}' ({fed_id}) unsubscribed from '{target_fed_name}' ({target_fed_id})"),
            fed_name=federation.fed_name,
            fed_id=federation.fed_id,
            target_fed_name=target_fed.fed_name,
            target_fed_id=target_fed.fed_id,
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, bot)
