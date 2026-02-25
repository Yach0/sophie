from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import In

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.db.models.federations import Federation, FederationBan, FederationExportTask
from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.modules.federations.exceptions import FederationBanValidationError
from sophie_bot.modules.federations.services.common import normalize_chat_iids
from sophie_bot.modules.federations.services.manage import FederationManageService
from sophie_bot.modules.federations.utils.cache_service import FederationCacheService
from sophie_bot.modules.restrictions.utils.restrictions import ban_user as restrict_ban_user
from sophie_bot.modules.restrictions.utils.restrictions import unban_user as restrict_unban_user


class FederationBanService:
    """Ban operations for federations."""

    @staticmethod
    async def ban_user(
        federation: Federation, user_tid: int, by_user_iid: PydanticObjectId, reason: Optional[str] = None
    ) -> FederationBan:
        existing_ban = await FederationBan.find_one(
            FederationBan.fed_id == federation.fed_id, FederationBan.user_id == user_tid
        )
        if existing_ban:
            if existing_ban.reason != reason:
                existing_ban.reason = reason
                await existing_ban.save()
            return existing_ban

        by_user = await ChatModel.get_by_iid(by_user_iid)
        if not by_user:
            raise FederationBanValidationError("Banner user not found")
        await FederationBanService.validate_ban_eligibility(federation, user_tid, by_user.tid)

        ban = FederationBan(
            fed_id=federation.fed_id,
            user_id=user_tid,
            time=datetime.now(timezone.utc),
            by=by_user_iid,
            reason=reason,
        )
        await ban.insert()
        await FederationCacheService.incr_ban_count(federation.fed_id, 1)

        await FederationBanService._invalidate_export_tasks(federation.fed_id)
        await FederationCacheService.set_user_ban_status(federation.fed_id, user_tid, True)
        return ban

    @staticmethod
    async def lazy_ban_in_subscribing_federations(
        origin_federation: Federation,
        user_tid: int,
        by_user_iid: PydanticObjectId,
        reason: Optional[str] = None,
    ) -> list[tuple[Federation, FederationBan]]:
        """Ban a user in federations that subscribe to the origin federation.

        This is called "lazy-ban" because it only bans the user in subscribing federations
        if the user is actually present in one of those federation's chats (tracked via
        UserInGroupModel).

        Returns a list of tuples (federation, ban_record) for each federation where
        the user was banned.
        """
        results: list[tuple[Federation, FederationBan]] = []

        # Find federations that subscribe TO this federation (transitive reverse lookup)
        # This includes direct subscribers and their subscribers (full chain)
        subscribing_feds = await FederationManageService.get_subscribed_by_chain(origin_federation.fed_id)
        if not subscribing_feds:
            return results

        # Get the user model
        user = await ChatModel.get_by_tid(user_tid)
        if not user:
            return results

        for sub_fed in subscribing_feds:
            # Skip if user is already banned in this federation
            existing_ban = await FederationBanService.is_user_banned(sub_fed.fed_id, user_tid)
            if existing_ban:
                continue

            # Check if federation has any chats
            if not sub_fed.chats:
                continue

            # Get chat IIDs for this federation
            sub_chat_iids = normalize_chat_iids([chat.to_ref() for chat in sub_fed.chats])
            if not sub_chat_iids:
                continue

            # Check if user is in any of this federation's chats via UserInGroupModel
            user_in_group = await UserInGroupModel.find(
                UserInGroupModel.user.id == user.iid,
                In(UserInGroupModel.group.id, sub_chat_iids),
            ).first_or_none()

            if user_in_group:
                # User is present in this federation's chat, create a ban record
                ban = FederationBan(
                    fed_id=sub_fed.fed_id,
                    user_id=user_tid,
                    time=datetime.now(timezone.utc),
                    by=by_user_iid,
                    reason=reason,
                    origin_fed=origin_federation.fed_id,
                )
                await ban.insert()
                await FederationCacheService.incr_ban_count(sub_fed.fed_id, 1)
                await FederationCacheService.set_user_ban_status(sub_fed.fed_id, user_tid, True)
                results.append((sub_fed, ban))

        return results

    @staticmethod
    async def ban_user_in_federation_chats(
        federation: Federation, ban: FederationBan, user_tid: int, current_chat_iid: PydanticObjectId | None = None
    ) -> int:
        if not federation.chats and not current_chat_iid:
            return 0

        chat_iids = normalize_chat_iids([chat.to_ref() for chat in federation.chats])
        if current_chat_iid and current_chat_iid not in chat_iids:
            chat_iids.append(current_chat_iid)

        chats = await ChatModel.find(In(ChatModel.iid, chat_iids)).to_list()
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

        banned_chat_iids: list[PydanticObjectId] = []

        sem = asyncio.Semaphore(15)

        async def _ban_task(chat):
            if chat.iid not in detected_chat_iids:
                return None
            async with sem:
                success = await restrict_ban_user(chat.tid, user_tid)
                return chat.iid if success else None

        tasks = [_ban_task(chat) for chat in chats]
        results = await asyncio.gather(*tasks)
        banned_chat_iids = [res for res in results if res is not None]

        if banned_chat_iids:
            if not ban.banned_chats:
                ban.banned_chats = []
            existing_chat_iids = set(normalize_chat_iids([chat.to_ref() for chat in ban.banned_chats]))
            for chat in chats:
                if chat.iid in banned_chat_iids and chat.iid not in existing_chat_iids:
                    ban.banned_chats.append(chat)
            await ban.save()

        return len(banned_chat_iids)

    @staticmethod
    async def unban_user(fed_id: str, user_tid: int) -> tuple[bool, Optional[FederationBan]]:
        result = await FederationBan.find_one(FederationBan.fed_id == fed_id, FederationBan.user_id == user_tid)
        if not result:
            return False, None

        if hasattr(result, "origin_fed") and result.origin_fed:
            return False, result

        await result.delete()
        await FederationCacheService.incr_ban_count(fed_id, -1)
        await FederationBanService._invalidate_export_tasks(fed_id)
        await FederationCacheService.set_user_ban_status(fed_id, user_tid, False)
        return True, None

    @staticmethod
    async def unban_user_in_federation_chats(federation: Federation, user_tid: int) -> int:
        return await FederationBanService.unban_user_in_federation_chats_with_subscribers(federation, user_tid)

    @staticmethod
    async def unban_user_in_federation_chats_with_subscribers(federation: Federation, user_tid: int) -> int:
        chat_iids: set[PydanticObjectId] = set()
        if federation.chats:
            chat_iids.update(normalize_chat_iids([chat.to_ref() for chat in federation.chats]))
        subscribing_feds = await Federation.find(Federation.subscribed == federation.fed_id).to_list()
        for sub_fed in subscribing_feds:
            if sub_fed.chats:
                chat_iids.update(normalize_chat_iids([chat.to_ref() for chat in sub_fed.chats]))
        if not chat_iids:
            return 0
        chats = await ChatModel.find(In(ChatModel.iid, list(chat_iids))).to_list()

        sem = asyncio.Semaphore(15)

        async def _unban_task(chat):
            async with sem:
                return await restrict_unban_user(chat.tid, user_tid)

        tasks = [_unban_task(chat) for chat in chats]
        results = await asyncio.gather(*tasks)
        return sum(1 for res in results if res)

    @staticmethod
    async def unban_user_in_chat_iids(chat_iids: list[object], user_tid: int) -> int:
        normalized_chat_iids = normalize_chat_iids(chat_iids)
        if not normalized_chat_iids:
            return 0
        chats = await ChatModel.find(In(ChatModel.iid, normalized_chat_iids)).to_list()

        sem = asyncio.Semaphore(15)

        async def _unban_task(chat):
            async with sem:
                return await restrict_unban_user(chat.tid, user_tid)

        tasks = [_unban_task(chat) for chat in chats]
        results = await asyncio.gather(*tasks)
        return sum(1 for res in results if res)

    @staticmethod
    async def get_federation_bans(fed_id: str) -> List[FederationBan]:
        return await FederationBan.find(FederationBan.fed_id == fed_id).to_list()

    @staticmethod
    async def is_user_banned(fed_id: str, user_tid: int) -> Optional[FederationBan]:
        return await FederationBan.find_one(FederationBan.fed_id == fed_id, FederationBan.user_id == user_tid)

    @staticmethod
    async def validate_ban_eligibility(federation: Federation, target_user_tid: int, banner_user_tid: int) -> None:
        if target_user_tid in CONFIG.operators:
            raise FederationBanValidationError("Cannot ban bot operators")
        if target_user_tid == banner_user_tid:
            raise FederationBanValidationError("You cannot ban yourself")
        if target_user_tid == CONFIG.bot_id:
            raise FederationBanValidationError("Cannot ban the bot")
        creator = await federation.creator.fetch()
        if creator and target_user_tid == creator.tid:
            raise FederationBanValidationError("Cannot ban the federation owner")
        if federation.admins:
            for admin_link in federation.admins:
                admin = await admin_link.fetch()
                if admin and target_user_tid == admin.tid:
                    raise FederationBanValidationError("Cannot ban federation administrators")

    @staticmethod
    async def _invalidate_export_tasks(fed_id: str) -> None:
        await FederationExportTask.find(
            FederationExportTask.fed_id == fed_id,
            FederationExportTask.status == TaskStatus.PENDING,
        ).update(
            {
                "$set": {
                    "status": TaskStatus.FAILED,
                    "error_message": "Ban list changed during export",
                    "completed_at": datetime.now(timezone.utc),
                }
            }
        )

    @staticmethod
    async def is_user_banned_in_chain(fed_id: str, user_tid: int) -> Optional[tuple[FederationBan, Federation]]:
        cached_status = await FederationCacheService.get_user_ban_status(fed_id, user_tid)
        if cached_status is False:
            return None

        chain_ids = await FederationManageService.get_subscription_chain(fed_id)
        chain_ids.append(fed_id)

        ban = await FederationBan.find(
            In(FederationBan.fed_id, chain_ids), FederationBan.user_id == user_tid
        ).first_or_none()

        if ban:
            await FederationCacheService.set_user_ban_status(fed_id, user_tid, True)
            banning_fed = await FederationManageService.get_federation_by_id(ban.fed_id)
            if banning_fed:
                return ban, banning_fed
        else:
            await FederationCacheService.set_user_ban_status(fed_id, user_tid, False)

        return None

    @staticmethod
    async def count_user_fed_bans(user_tid: int) -> int:
        fed_ids: set[str] = set()
        async for ban in FederationBan.find(FederationBan.user_id == user_tid):
            fed_ids.add(ban.fed_id)
        return len(fed_ids)

    @staticmethod
    async def get_user_fed_bans(
        user_tid: int,
        only_with_banned_chats: bool = True,
    ) -> list[tuple[FederationBan, Federation]]:
        bans = await FederationBan.find(FederationBan.user_id == user_tid).to_list()
        results: list[tuple[FederationBan, Federation]] = []
        for ban in bans:
            if only_with_banned_chats and not ban.banned_chats:
                continue
            federation = await FederationManageService.get_federation_by_id(ban.fed_id)
            if federation:
                results.append((ban, federation))
        return results

    @staticmethod
    async def get_federation_ban_count(fed_id: str) -> int:
        cached = await FederationCacheService.get_ban_count(fed_id)
        if cached is not None:
            return cached
        count = await FederationBan.find(FederationBan.fed_id == fed_id).count()
        await FederationCacheService.set_ban_count(fed_id, count)
        return count
