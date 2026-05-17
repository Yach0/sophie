from __future__ import annotations

import asyncio
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg, TextArg
from stfu_tg import Code, Doc, KeyValue, Template, Title, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.constants import SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.federations.exceptions import FederationBanValidationError
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services import FederationBanService, FederationManageService
from sophie_bot.modules.federations.services.common import normalize_chat_iids
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.modules.restrictions.utils.logging import extract_offending_message_text
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


async def delete_messages_after_delay(
    chat_id: int,
    message_ids: list[int],
    delay_seconds: int = SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS,
) -> None:
    """Delete messages after a specified delay."""
    await asyncio.sleep(delay_seconds)
    await common_try(bot.delete_messages(chat_id, message_ids))


def _build_ban_reply_doc(
    federation: Federation,
    user: ChatModel,
    banner,
    reason: str | None,
    banned_count: int,
    lazy_ban_count: int,
    silent: bool,
) -> Doc:
    """Format the user-facing ban response document."""
    doc = Doc(
        Title(_("🏛 User Banned from Federation")),
        KeyValue(_("Federation"), federation.fed_name),
        KeyValue(_("User"), UserLink(user.tid, user.first_name_or_title or _("Unknown"))),
        KeyValue(_("Banned by"), UserLink(banner.id, banner.first_name)),
    )
    if reason:
        doc += KeyValue(_("Reason"), reason)
    doc += KeyValue(_("Result"), Template(_("Banned in {count} chats"), count=Code(banned_count)))

    if lazy_ban_count > 0:
        doc += KeyValue(_("Also banned in"), Template(_("{count} subscribed federations"), count=Code(lazy_ban_count)))

    if silent:
        doc += _("The action is silent, all related messages would be deleted shortly")

    return doc


def _build_ban_log_doc(
    federation: Federation,
    user: ChatModel,
    banner,
    banned_count: int,
    total_chats: int,
    reason: str | None,
    original_message_text: str | None,
) -> Doc:
    """Format the federation log entry document."""
    log_doc = Doc(
        Title(_("Ban user in the fed #FedBan")),
        KeyValue(_("Fed"), Template("{fed_name} ({fed_id})", fed_name=federation.fed_name, fed_id=federation.fed_id)),
        KeyValue(
            _("User"),
            Template(
                "{user_name} ({user_id})",
                user_name=user.first_name_or_title or _("Unknown"),
                user_id=Code(user.tid),
            ),
        ),
        KeyValue(_("By"), banner.first_name),
        Template(
            "User banned in {banned_count} out of {total_chats} chats in the federation",
            banned_count=banned_count,
            total_chats=total_chats,
        ),
    )
    if reason:
        log_doc += KeyValue(_("Reason"), reason)
    if original_message_text:
        log_doc += KeyValue(_("Original message"), original_message_text)
    return log_doc


@flags.help(description=l_("Ban a user from the federation"))
class FederationBanHandler(FederationCommandHandler):
    """Handler for banning users from federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("fban", "sfban")),
            FeatureFlagFilter("new_feds_fban"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        """Define arguments for the fban command."""
        base_args = await super().handler_args(message, data)
        base_args.update(
            {
                "user": OptionalArg(SophieUserArg(l_("User"), allow_unknown_id=True)),
                "reason": OptionalArg(TextArg(l_("?Reason"))),
            }
        )
        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Ban user from federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

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

        user_tid = user.tid

        # Permission check
        banner_tid = self.event.from_user.id if self.event.from_user else 0
        if not await FederationPermissionService.can_ban_in_federation(federation, banner_tid):
            await self.event.reply(_("You don't have permission to ban users in this federation."))
            return

        original_message_text = extract_offending_message_text(self.event.reply_to_message)

        # Generate AI reason if none provided and replying to a message (federations don't include group rules)
        if not reason and self.event.reply_to_message:
            replied_text = original_message_text
            ai_reason = await generate_restriction_reason(
                self.connection.db_model,
                message_text=replied_text,
                include_rules=False,
            )
            if ai_reason:
                reason = ai_reason

        # Ban user
        user_iid = self.data["user_db"].iid
        try:
            ban = await FederationBanService.ban_user(federation, user_tid, user_iid, reason, original_message_text)
        except FederationBanValidationError as err:
            await self.event.reply(str(err))
            return

        # Is current chat part of the federation?
        federation_chat_iids = (
            normalize_chat_iids([chat.to_ref() for chat in federation.chats]) if federation.chats else []
        )
        chat_part_of_federation: bool = self.connection.db_model.iid in federation_chat_iids

        banned_count = await FederationBanService.ban_user_in_federation_chats(
            federation,
            ban,
            user_tid,
            # Ban user in current chat if it's part of the federation
            current_chat_iid=(self.connection.db_model.iid if chat_part_of_federation else None),
        )

        # Lazy-ban: Also ban in federations that subscribe to this federation
        lazy_ban_count = 0
        if await is_enabled("new_feds_fban_lazy", chat_tid=self.connection.db_model.tid):
            lazy_bans = await FederationBanService.lazy_ban_in_subscribing_federations(
                federation, user_tid, user_iid, reason, original_message_text
            )
            lazy_ban_count = len(lazy_bans)

        # Format response
        silent = bool(self.event.text and self.event.text.startswith("/sfban"))
        doc = _build_ban_reply_doc(federation, user, self.event.from_user, reason, banned_count, lazy_ban_count, silent)

        reply_msg = await common_try(
            self.event.reply(doc.to_html()),
            reply_not_found=lambda: self.event.answer(doc.to_html()),
        )

        # If silent mode, schedule deletion of messages after SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
        if silent:
            messages_to_delete = [self.event.message_id, reply_msg.message_id]
            if self.event.reply_to_message:
                messages_to_delete.append(self.event.reply_to_message.message_id)
            asyncio.create_task(delete_messages_after_delay(self.event.chat.id, messages_to_delete))

        # Log the ban
        total_chats = len(federation.chats) if federation.chats else 0
        log_doc = _build_ban_log_doc(
            federation,
            user,
            self.event.from_user,
            banned_count,
            total_chats,
            reason,
            original_message_text,
        )
        await FederationManageService.post_federation_log(federation, log_doc.to_html(), self.event.bot)
