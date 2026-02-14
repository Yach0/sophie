from __future__ import annotations

import asyncio
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg, TextArg
from stfu_tg import Code, Doc, KeyValue, Template, Title, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.models import ChatModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.args.fed_id import FedIdArg
from sophie_bot.modules.federations.exceptions import FederationBanValidationError
from sophie_bot.modules.federations.services.federation import FederationService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


async def delete_messages_after_delay(
    chat_id: int,
    message_ids: list[int],
    delay_seconds: int = 10,
) -> None:
    """Delete messages after a specified delay."""
    await asyncio.sleep(delay_seconds)
    await common_try(bot.delete_messages(chat_id, message_ids))


@flags.help(description=l_("Ban a user from the federation"))
@flags.disableable(name="fban")
class FederationBanHandler(SophieMessageHandler):
    """Handler for banning users from federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("fban", "sfban")), FeatureFlagFilter("new_feds_fban"))

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {
            "fed_id": OptionalArg(
                FedIdArg(l_("Federation ID (optional, uses current chat's federation if not specified)"))
            ),
            "user": OptionalArg(SophieUserArg(l_("User to ban"))),
            "reason": OptionalArg(TextArg(l_("Reason (optional)"))),
        }

    async def handle(self) -> Any:
        """Ban user from federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        fed_id_arg: str | None = self.data.get("fed_id")
        user: ChatModel | None = self.data.get("user")
        reason: str | None = self.data.get("reason")

        if not user:
            reply_message = self.event.reply_to_message
            reply_from_user = reply_message.from_user if reply_message else None
            if not reply_from_user:
                await self.event.reply(_("Please specify a user or reply to a user's message."))
                return
            user = await ChatModel.get_by_tid(reply_from_user.id)
            if not user:
                user = await ChatModel.upsert_user(reply_from_user)

        # Determine federation
        user_tid = user.tid
        if fed_id_arg:
            federation = await FederationService.get_federation_by_id(fed_id_arg)
            if not federation:
                await self.event.reply(_("Federation not found."))
                return
        else:
            # Use current chat's federation
            chat_iid = self.connection.db_model.iid
            federation = await FederationService.get_federation_for_chat(chat_iid)
            if not federation:
                await self.event.reply(
                    Template(
                        _("This chat is not in any federation. Use {cmd} to specify federation."),
                        cmd=Code("/fban <fed_id> <user>"),
                    ).to_html()
                )
                return

        # Permission check
        banner_tid = self.event.from_user.id if self.event.from_user else 0
        if not await FederationPermissionService.can_ban_in_federation(federation, banner_tid):
            await self.event.reply(_("You don't have permission to ban users in this federation."))
            return

        # Ban user
        try:
            user_iid = self.data["user_db"].iid
            ban = await FederationService.ban_user(federation, user_tid, user_iid, reason)
        except FederationBanValidationError as e:
            await self.event.reply(str(e))
            return
        banned_count = await FederationService.ban_user_in_federation_chats(federation, ban, user_tid)

        # Format response
        silent = self.event.text and self.event.text.startswith("/sfban")
        doc = Doc(
            Title(_("🏛 User Banned from Federation")),
            KeyValue(_("Federation"), federation.fed_name),
            KeyValue(_("User"), UserLink(user.tid, user.first_name_or_title or _("Unknown"))),
            KeyValue(_("Banned by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
        )
        if reason:
            doc += KeyValue(_("Reason"), reason)
        doc += KeyValue(_("Result"), Template(_("Banned in {count} chats"), count=str(banned_count)))

        reply_msg = await self.event.reply(str(doc))

        # If silent mode, schedule deletion of messages after 10 seconds
        if silent:
            messages_to_delete = [self.event.message_id, reply_msg.message_id]
            if self.event.reply_to_message:
                messages_to_delete.append(self.event.reply_to_message.message_id)
            asyncio.create_task(delete_messages_after_delay(self.event.chat.id, messages_to_delete))

        # Log the ban
        total_chats = len(federation.chats) if federation.chats else 0

        log_doc = Doc(
            Title(_("Ban user in the fed #FedBan")),
            KeyValue(
                _("Fed"), Template("{fed_name} ({fed_id})", fed_name=federation.fed_name, fed_id=federation.fed_id)
            ),
            KeyValue(
                _("User"),
                Template(
                    "{user_name} ({user_id})", user_name=user.first_name_or_title or _("Unknown"), user_id=user.tid
                ),
            ),
            KeyValue(_("By"), self.event.from_user.first_name),
            KeyValue(
                _("Chats banned"),
                Template(
                    "user banned in {banned_count}/{total_chats} chats",
                    banned_count=banned_count,
                    total_chats=total_chats,
                ),
            ),
        )
        if reason:
            log_doc += KeyValue(_("Reason"), reason)
        await FederationService.post_federation_log(federation, log_doc.to_html(), self.event.bot)
