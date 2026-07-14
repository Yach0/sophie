from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Optional, TypeVar

from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import In

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, CommunityBanModel
from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.modules.communities.exceptions import CommunityBanValidationError
from sophie_bot.modules.federations.services.common import normalize_chat_iids
from sophie_bot.modules.restrictions.utils.restrictions import ban_user as restrict_ban_user
from sophie_bot.modules.restrictions.utils.restrictions import unban_user as restrict_unban_user

ChatActionResultT = TypeVar("ChatActionResultT")


class CommunityBanService:
    """Ban operations scoped to a Telegram community."""

    @staticmethod
    async def ban_user(
        community_tid: int,
        user_tid: int,
        by_user_iid: PydanticObjectId,
        reason: Optional[str] = None,
        original_message_text: Optional[str] = None,
    ) -> CommunityBanModel:
        existing_ban = await CommunityBanModel.find_one(
            CommunityBanModel.community_tid == community_tid, CommunityBanModel.user_id == user_tid
        )
        if existing_ban:
            if existing_ban.reason != reason or existing_ban.original_message_text != original_message_text:
                existing_ban.reason = reason
                existing_ban.original_message_text = original_message_text
                await existing_ban.save()
            return existing_ban

        by_user = await ChatModel.get_by_iid(by_user_iid)
        if not by_user:
            raise CommunityBanValidationError("Banner user not found")
        CommunityBanService.validate_ban_eligibility(user_tid, by_user.tid)

        ban = CommunityBanModel(
            community_tid=community_tid,
            user_id=user_tid,
            time=datetime.now(timezone.utc),
            by=by_user_iid,
            reason=reason,
            original_message_text=original_message_text,
        )
        await ban.insert()
        return ban

    @staticmethod
    async def ban_user_in_community_chats(
        community_tid: int,
        ban: CommunityBanModel,
        user_tid: int,
        current_chat_iid: PydanticObjectId | None = None,
    ) -> int:
        chats = await ChatModel.find(ChatModel.community_tid == community_tid).to_list()
        chat_iids = [chat.iid for chat in chats]
        if current_chat_iid and current_chat_iid not in chat_iids:
            extra_chat = await ChatModel.get_by_iid(current_chat_iid)
            if extra_chat:
                chats.append(extra_chat)
                chat_iids.append(current_chat_iid)

        if not chats:
            return 0

        user = await ChatModel.get_by_tid(user_tid)
        if not user:
            return 0

        user_in_groups = await UserInGroupModel.find(
            UserInGroupModel.user.id == user.iid,
            In(UserInGroupModel.group.id, chat_iids),
        ).to_list()
        detected_chat_iids = set(
            normalize_chat_iids([user_in_group.group.to_ref() for user_in_group in user_in_groups])
        )
        if current_chat_iid:
            detected_chat_iids.add(current_chat_iid)

        async def ban_chat(chat: ChatModel) -> PydanticObjectId | None:
            if chat.iid not in detected_chat_iids:
                return None
            success = await restrict_ban_user(chat.tid, user_tid)
            return chat.iid if success else None

        banned_chat_iids = await CommunityBanService._run_limited_chat_actions(chats, ban_chat)

        if banned_chat_iids:
            existing_chat_iids = set(normalize_chat_iids([chat.to_ref() for chat in ban.banned_chats]))
            for chat in chats:
                if chat.iid in banned_chat_iids and chat.iid not in existing_chat_iids:
                    ban.banned_chats.append(chat)
            await ban.save()

        return len(banned_chat_iids)

    @staticmethod
    async def unban_user(community_tid: int, user_tid: int) -> Optional[CommunityBanModel]:
        """Remove the community ban record. Returns the deleted record (with banned_chats)."""
        ban = await CommunityBanModel.find_one(
            CommunityBanModel.community_tid == community_tid, CommunityBanModel.user_id == user_tid
        )
        if not ban:
            return None
        await ban.delete()
        return ban

    @staticmethod
    async def unban_user_in_chat_iids(chat_iids: list[PydanticObjectId], user_tid: int) -> int:
        if not chat_iids:
            return 0
        chats = await ChatModel.find(In(ChatModel.iid, chat_iids)).to_list()

        async def unban_chat(chat: ChatModel) -> bool:
            return await restrict_unban_user(chat.tid, user_tid)

        results = await CommunityBanService._run_limited_chat_actions(chats, unban_chat)
        return sum(1 for result in results if result)

    @staticmethod
    async def is_user_banned(community_tid: int, user_tid: int) -> Optional[CommunityBanModel]:
        return await CommunityBanModel.find_one(
            CommunityBanModel.community_tid == community_tid, CommunityBanModel.user_id == user_tid
        )

    @staticmethod
    def validate_ban_eligibility(target_user_tid: int, banner_user_tid: int) -> None:
        if target_user_tid in CONFIG.operators:
            raise CommunityBanValidationError("Cannot ban bot operators")
        if target_user_tid == banner_user_tid:
            raise CommunityBanValidationError("You cannot ban yourself")
        if target_user_tid == CONFIG.bot_id:
            raise CommunityBanValidationError("Cannot ban the bot")

    @staticmethod
    async def _run_limited_chat_actions(
        chats: list[ChatModel],
        action: Callable[[ChatModel], Awaitable[ChatActionResultT | None]],
        limit: int = 15,
    ) -> list[ChatActionResultT]:
        semaphore = asyncio.Semaphore(limit)

        async def run_action(chat: ChatModel) -> ChatActionResultT | None:
            async with semaphore:
                return await action(chat)

        results = await asyncio.gather(*(run_action(chat) for chat in chats))
        return [result for result in results if result is not None]
