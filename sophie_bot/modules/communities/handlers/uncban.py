from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.communities import CommunityTask
from sophie_bot.db.models.communities_enums import CommunityTaskType
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.communities.services import CommunityBanService, CommunityManageService
from sophie_bot.modules.communities.utils.ban_docs import build_unban_reply_doc
from sophie_bot.modules.federations.services.common import normalize_chat_iids
from sophie_bot.modules.restrictions.utils.restrictions import unban_user as restrict_unban_user
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Unban a user from the whole community."))
class CommunityUnbanHandler(SophieMessageHandler):
    """Unban a user across every chat of the current chat's community."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("uncban", "cunban")),
            FeatureFlagFilter("communities"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {
            "user": OptionalArg(SophieUserArg(l_("User"), allow_unknown_id=True)),
        }

    async def handle(self) -> Any:
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        current_chat = self.connection.db_model
        community = await CommunityManageService.get_community_for_chat(current_chat)
        if not community:
            await self.event.reply(_("This chat is not part of a community."))
            return

        user: ChatModel | None = self.data.get("user")
        if not user:
            reply_message = self.event.reply_to_message
            reply_from_user = reply_message.from_user if reply_message else None
            if not reply_from_user:
                await self.event.reply(_("Please specify a user or reply to a user's message."))
                return
            user = await ChatModel.get_by_tid(reply_from_user.id) or ChatModel.get_user_model(reply_from_user)

        banner = await ChatModel.get_by_tid(self.event.from_user.id)
        if not banner:
            await self.event.reply(_("Could not resolve the command user. Please try again."))
            return

        existing_ban = await CommunityBanService.is_user_banned(community.community_tid, user.tid)
        if not existing_ban:
            await self.event.reply(_("This user is not banned in this community."))
            return

        # Snapshot the chats the user was actually banned in before the record is removed.
        unban_chat_iids = (
            normalize_chat_iids([chat.to_ref() for chat in existing_ban.banned_chats])
            if existing_ban.banned_chats
            else []
        )

        await CommunityBanService.unban_user(community.community_tid, user.tid)

        # Unban in the current chat right away; the scheduler propagates to the rest.
        immediate_chat_unbanned = await restrict_unban_user(self.event.chat.id, user.tid)

        doc = build_unban_reply_doc(
            community,
            user,
            self.event.from_user.id,
            self.event.from_user.first_name,
            propagating=True,
            immediate_chat_unbanned=immediate_chat_unbanned,
        )
        reply_msg = await common_try(
            self.event.reply(doc.to_html()),
            reply_not_found=lambda: self.event.answer(doc.to_html()),
        )

        await CommunityTask(
            community_tid=community.community_tid,
            task_type=CommunityTaskType.UNBAN,
            target_user_id=user.tid,
            user=banner.iid,
            chat=current_chat.iid,
            current_chat_iid=current_chat.iid,
            reply_chat_id=self.event.chat.id,
            reply_message_id=reply_msg.message_id if reply_msg else None,
            unban_chat_iids=unban_chat_iids,
            created_at=datetime.now(timezone.utc),
        ).insert()
