from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import Doc, Title, Template

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Sets the Federation logs channel"))
class SetFederationLogHandler(SophieMessageHandler):
    """Handler for setting federation log channel."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("fsetlog", "setfedlog")), FeatureFlagFilter("new_feds_setlog")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {}

    async def handle(self) -> Any:
        """Set federation log channel."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        # Get federation for this chat
        chat_iid = self.connection.db_model.iid
        federation = await FederationManageService.get_federation_for_chat(chat_iid)
        if not federation:
            await self.event.reply(_("This chat is not in any federation."))
            return

        # Check if user is federation owner
        creator = await federation.creator.fetch()
        user_iid = self.data["user_db"].iid
        if not creator or creator.iid != user_iid:
            await self.event.reply(_("Only the federation owner can set the log channel."))
            return

        # Check if log channel is already set
        if federation.log_chat:
            await self.event.reply(
                _("This federation already has a log channel set. Use /funsetlog to remove it first.")
            )
            return

        # For channels, we need to check if bot can post
        if self.connection.type == "channel":
            # Check if bot has permission to post in this channel
            bot = self.event.bot
            if not bot:
                await self.event.reply(_("Unable to verify permissions in this channel."))
                return
            try:
                bot_member = await bot.get_chat_member(self.connection.tid, bot.id)
                # can_post_messages is only available on ChatMemberAdministrator and ChatMemberOwner
                if not hasattr(bot_member, "can_post_messages") or not bot_member.can_post_messages:
                    await self.event.reply(_("I don't have permission to post messages in this channel."))
                    return
            except Exception:
                await self.event.reply(_("Unable to verify permissions in this channel."))
                return

        # Set the log channel
        await FederationManageService.set_federation_log_channel(federation, chat_iid)

        # Send confirmation
        doc = Doc(
            Title(_("🏛 Federation Log Channel Set")),
            Template(_("Log channel has been set for federation '{name}'."), name=federation.fed_name),
            _("All federation actions will now be logged here."),
        )
        await self.event.reply(str(doc))

        # Post log message
        log_text = Template(
            _("🏛 Federation '{name}' log channel has been set to this chat."),
            name=federation.fed_name,
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)


@flags.help(description=l_("Removes the Federation logs channel"))
class UnsetFederationLogHandler(SophieMessageHandler):
    """Handler for removing federation log channel."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("funsetlog", "unsetfedlog")), FeatureFlagFilter("new_feds_unsetlog"))

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {}

    async def handle(self) -> Any:
        """Remove federation log channel."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        # Get federation for this chat
        chat_iid = self.connection.db_model.iid
        federation = await FederationManageService.get_federation_for_chat(chat_iid)
        if not federation:
            await self.event.reply(_("This chat is not in any federation."))
            return

        # Check if user is federation owner
        creator = await federation.creator.fetch()
        user_iid = self.data["user_db"].iid
        if not creator or creator.iid != user_iid:
            await self.event.reply(_("Only the federation owner can remove the log channel."))
            return

        # Check if log channel is set
        if not federation.log_chat:
            await self.event.reply(_("This federation doesn't have a log channel set."))
            return

        # Post log message before removing
        log_text = Template(
            _("🏛 Federation '{name}' log channel has been removed."), name=federation.fed_name
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)

        # Remove log channel
        await FederationManageService.remove_federation_log_channel(federation)

        # Send confirmation
        doc = Doc(
            Title(_("🏛 Federation Log Channel Removed")),
            Template(_("Log channel has been removed for federation '{name}'."), name=federation.fed_name),
        )
        await self.event.reply(str(doc))
