from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, cast

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from beanie import Link, PydanticObjectId
from beanie.odm.operators.find.comparison import In
from bson import DBRef

from sophie_bot.config import CONFIG
from sophie_bot.constants import MAX_FEDERATION_NAME_LENGTH, MAX_FEDERATIONS_PER_USER
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation, FederationBan, FederationExportTask
from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.federations.exceptions import (
    FederationAlreadyExistsError,
    FederationBanValidationError,
    FederationContextError,
    FederationLimitExceededError,
    FederationNotFoundError,
)
from sophie_bot.modules.federations.utils.cache_service import FederationCacheService
from sophie_bot.modules.restrictions.utils.restrictions import ban_user as restrict_ban_user
from sophie_bot.modules.restrictions.utils.restrictions import unban_user as restrict_unban_user
from sophie_bot.utils.i18n import gettext as _


class FederationService:
    """Business logic for federation operations."""

    @staticmethod
    def _normalize_chat_iids(chat_refs: list[object]) -> list[PydanticObjectId]:
        normalized: list[PydanticObjectId] = []
        for chat_ref in chat_refs:
            if isinstance(chat_ref, PydanticObjectId):
                normalized.append(chat_ref)
            elif isinstance(chat_ref, DBRef):
                normalized.append(cast(PydanticObjectId, chat_ref.id))
            elif isinstance(chat_ref, dict):
                dict_ref = cast(dict[str, object], chat_ref)
                chat_id = dict_ref.get("$id")
                if chat_id is not None:
                    normalized.append(cast(PydanticObjectId, chat_id))
                else:
                    normalized.append(cast(PydanticObjectId, chat_ref))
            else:
                normalized.append(cast(PydanticObjectId, chat_ref))
        return normalized

    @staticmethod
    async def create_federation(name: str, creator_iid: PydanticObjectId) -> Federation:
        """Create a new federation.

        Args:
            name: Federation name
            creator_iid: Internal DB ID of the federation creator

        Returns:
            The created Federation object

        Raises:
            FederationValidationError: If name is invalid
            FederationLimitExceededError: If user exceeded federation creation limit
            FederationAlreadyExistsError: If federation name already exists
        """
        # Validate name
        if len(name) > MAX_FEDERATION_NAME_LENGTH:
            raise FederationLimitExceededError("Federation name too long")

        # Check if user can create federation
        if not await FederationService._can_user_create_federation(creator_iid):
            raise FederationLimitExceededError("Federation creation limit exceeded")

        # Check name uniqueness
        if await Federation.find_one(Federation.fed_name == name):
            raise FederationAlreadyExistsError("Federation with this name already exists")

        # Create federation
        federation = Federation(fed_name=name, fed_id=str(uuid.uuid4()), creator=creator_iid)
        await federation.insert()

        return federation

    @staticmethod
    async def get_federation_by_id(fed_id: str) -> Optional[Federation]:
        """Get federation by ID (with caching)."""
        cached_data = await FederationCacheService.get_federation_by_id(fed_id)
        if cached_data:
            return Federation(**cached_data)

        federation = await FederationService._get_federation_by_id_direct(fed_id)
        if federation:
            await FederationCacheService._cache_federation(federation)
        return federation

    @staticmethod
    async def _get_federation_by_id_direct(fed_id: str) -> Optional[Federation]:
        """Get federation by ID directly from database (bypasses cache)."""
        return await Federation.find_one(Federation.fed_id == fed_id)

    @staticmethod
    async def get_federation_by_creator(creator_iid: PydanticObjectId) -> Optional[Federation]:
        """Get federation created by user."""
        return await Federation.find_one(Federation.creator.id == creator_iid)

    @staticmethod
    async def get_federations_by_creator(creator_iid: PydanticObjectId) -> list[Federation]:
        """Get all federations created by user."""
        return await Federation.find(Federation.creator.id == creator_iid).to_list()

    @staticmethod
    async def get_federation_for_chat(chat_iid: PydanticObjectId) -> Optional[Federation]:
        """Get federation that contains the chat (with caching)."""
        cached_data = await FederationCacheService.get_federation_for_chat(chat_iid)
        if cached_data:
            return Federation(**cached_data)

        federation = await FederationService._get_federation_for_chat_direct(chat_iid)
        if federation:
            await FederationCacheService._cache_federation(federation)
        return federation

    @staticmethod
    async def _get_federation_for_chat_direct(chat_iid: PydanticObjectId) -> Optional[Federation]:
        """Get federation that contains the chat directly from database (bypasses cache)."""

        return await Federation.find_one(Federation.chats == DBRef(ChatModel.Settings.name, chat_iid))

    @staticmethod
    async def get_federation(
        fed_id_arg: str | None,
        connection: ChatConnection | None = None,
        user_id: int | None = None,
    ) -> Federation:
        """
        Get federation based on provided context.

        Priority order:
        1. If fed_id_arg is provided, try to get that federation
        2. If connection targets a chat (connected or direct chat), use that chat's federation
        3. If user_id is provided (PM context) and user has exactly 1 federation, use that federation

        Args:
            fed_id_arg: Optional federation ID to explicitly select
            connection: Optional chat connection context
            user_id: Optional Telegram user ID for PM context

        Returns:
            The Federation object

        Raises:
            FederationNotFoundError: If federation cannot be found or determined
            FederationContextError: If context is ambiguous or insufficient
        """
        # If fed_id is provided, try to get that federation
        if fed_id_arg:
            federation = await FederationService.get_federation_by_id(fed_id_arg)
            if not federation:
                raise FederationNotFoundError("Federation not found")
            return federation

        # If in a group chat, try to get federation for that chat
        if connection and (connection.is_connected or connection.type != ChatType.private):
            federation = await FederationService.get_federation_for_chat(connection.db_model.iid)
            if federation:
                return federation
            raise FederationContextError(_("This chat is not in any federation"))

        # If in PM context, check if user has exactly 1 federation
        if user_id:
            user = await ChatModel.get_by_tid(user_id)
            if not user:
                raise FederationContextError(_("User not found in database"))
            user_federations = await FederationService.get_federations_by_creator(user.iid)
            if len(user_federations) == 1:
                return user_federations[0]
            elif len(user_federations) > 1:
                raise FederationContextError(_("You have multiple federations"))
            else:
                raise FederationContextError(_("You don't have any federations"))

        raise FederationContextError(_("Could not determine federation"))

    @staticmethod
    async def update_federation(federation: Federation, updates: dict) -> Federation:
        """Update federation with new data."""
        for key, value in updates.items():
            setattr(federation, key, value)
        await federation.save()
        await FederationCacheService.invalidate_federation(federation.fed_id)
        return federation

    @staticmethod
    async def delete_federation(federation: Federation) -> None:
        """Delete federation and all related data."""
        # Delete federation bans
        await FederationBan.find(FederationBan.fed_id == federation.fed_id).delete()

        # Invalidate cache
        await FederationCacheService.invalidate_federation(federation.fed_id)
        if federation.chats:
            chats = await ChatModel.find(In(ChatModel.iid, [c.to_ref() for c in federation.chats])).to_list()
            for chat in chats:
                await FederationCacheService.invalidate_federation_for_chat(chat.iid)

        # Delete federation
        await federation.delete()

    @staticmethod
    async def add_chat_to_federation(federation: Federation, chat_iid: PydanticObjectId) -> None:
        """Add chat to federation."""
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return

        if chat.iid not in [c.to_ref() for c in federation.chats]:
            federation.chats.append(chat)
            await federation.save()
            await FederationCacheService.invalidate_federation(federation.fed_id)
            await FederationCacheService.invalidate_federation_for_chat(chat.iid)

    @staticmethod
    async def remove_chat_from_federation(federation: Federation, chat_iid: PydanticObjectId) -> None:
        """Remove chat from federation."""
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return

        for c in federation.chats:
            if c.to_ref() == chat_iid:
                federation.chats.remove(c)
                await federation.save()
                await FederationCacheService.invalidate_federation(federation.fed_id)
                await FederationCacheService.invalidate_federation_for_chat(chat.iid)
                break

    @staticmethod
    async def get_federation_chat_count(fed_id: str) -> int:
        """Get number of chats in federation."""
        federation = await FederationService.get_federation_by_id(fed_id)
        if not federation or not federation.chats:
            return 0
        return len(federation.chats)

    @staticmethod
    async def get_federation_ban_count(fed_id: str) -> int:
        """Get number of banned users in federation."""
        return await FederationBan.find(FederationBan.fed_id == fed_id).count()

    @staticmethod
    async def ban_user(
        federation: Federation, user_tid: int, by_user_iid: PydanticObjectId, reason: Optional[str] = None
    ) -> FederationBan:
        """Ban user from federation and all subscribed federations."""
        # Check if already banned in this federation
        existing_ban = await FederationBan.find_one(
            FederationBan.fed_id == federation.fed_id, FederationBan.user_id == user_tid
        )
        if existing_ban:
            # Update reason if different
            if existing_ban.reason != reason:
                existing_ban.reason = reason
                await existing_ban.save()
            return existing_ban

        # Validate ban eligibility
        by_user = await ChatModel.get_by_iid(by_user_iid)
        if not by_user:
            raise FederationBanValidationError("Banner user not found")
        await FederationService.validate_ban_eligibility(federation, user_tid, by_user.tid)

        # Create new ban in this federation
        ban = FederationBan(
            fed_id=federation.fed_id,
            user_id=user_tid,
            time=datetime.now(timezone.utc),
            by=by_user_iid,
            reason=reason,
        )
        await ban.insert()

        # Ban in all subscribed federations (but don't create DB entries - middleware handles enforcement)
        # This ensures ban propagates through the subscription chain
        subscription_chain = await FederationService.get_subscription_chain(federation.fed_id)
        for sub_fed_id in subscription_chain:
            # Check if user is already banned in subscribed federation
            existing_sub_ban = await FederationBan.find_one(
                FederationBan.fed_id == sub_fed_id, FederationBan.user_id == user_tid
            )
            if not existing_sub_ban:
                # Create ban in subscribed federation with origin_fed pointing to this federation
                sub_ban = FederationBan(
                    fed_id=sub_fed_id,
                    user_id=user_tid,
                    time=datetime.now(timezone.utc),
                    by=by_user_iid,
                    reason=reason,
                    origin_fed=federation.fed_id,  # Mark this as originating from subscription
                )
                await sub_ban.insert()

        await FederationService._invalidate_export_tasks(federation.fed_id)
        return ban

    @staticmethod
    async def ban_user_in_federation_chats(
        federation: Federation, ban: FederationBan, user_tid: int, current_chat_iid: PydanticObjectId | None = None
    ) -> int:
        """Ban user in all federation chats and track successful bans.

        Args:
            federation: The federation to ban user from
            ban: The FederationBan record
            user_tid: Telegram user ID to ban
            current_chat_iid: Optional chat ID to ensure ban even if not in federation.chats list yet

        Returns:
            Number of chats where user was successfully banned
        """
        if not federation.chats and not current_chat_iid:
            return 0

        chat_iids = FederationService._normalize_chat_iids([chat.to_ref() for chat in federation.chats])

        if current_chat_iid and current_chat_iid not in chat_iids:
            chat_iids.append(current_chat_iid)

        chats = await ChatModel.find(In(ChatModel.iid, chat_iids)).to_list()

        banned_chat_iids: list[PydanticObjectId] = []
        for chat in chats:
            if await restrict_ban_user(chat.tid, user_tid):
                banned_chat_iids.append(chat.iid)

        if banned_chat_iids:
            if not ban.banned_chats:
                ban.banned_chats = []
            existing_chat_iids = set(
                FederationService._normalize_chat_iids([chat.to_ref() for chat in ban.banned_chats])
            )
            for chat in chats:
                if chat.iid in banned_chat_iids and chat.iid not in existing_chat_iids:
                    ban.banned_chats.append(chat)
            await ban.save()

        return len(banned_chat_iids)

    @staticmethod
    async def unban_user(fed_id: str, user_tid: int) -> tuple[bool, Optional[FederationBan]]:
        """Unban user from federation. Returns (success, ban_info_if_from_subscription)."""
        result = await FederationBan.find_one(FederationBan.fed_id == fed_id, FederationBan.user_id == user_tid)
        if not result:
            return False, None

        # If this ban originated from a subscription, don't allow unbanning
        if hasattr(result, "origin_fed") and result.origin_fed:
            return False, result

        # Delete ban
        await result.delete()

        # Also unban from federations that subscribe to this one
        # Find all federations that have this fed_id in their subscribed list
        subscribing_feds = await Federation.find(Federation.subscribed == fed_id).to_list()
        for sub_fed in subscribing_feds:
            # Only unban if ban in the subscribing fed originated from this federation
            sub_ban = await FederationBan.find_one(
                FederationBan.fed_id == sub_fed.fed_id,
                FederationBan.user_id == user_tid,
                FederationBan.origin_fed == fed_id,
            )
            if sub_ban:
                await sub_ban.delete()

        await FederationService._invalidate_export_tasks(fed_id)
        return True, None

    @staticmethod
    async def unban_user_in_federation_chats(federation: Federation, user_tid: int) -> int:
        """Unban user in all federation chats."""
        return await FederationService.unban_user_in_federation_chats_with_subscribers(federation, user_tid)

    @staticmethod
    async def unban_user_in_federation_chats_with_subscribers(federation: Federation, user_tid: int) -> int:
        """Unban user in all chats of this federation and federations that subscribe to it."""
        chat_iids: set[PydanticObjectId] = set()

        if federation.chats:
            chat_iids.update(FederationService._normalize_chat_iids([chat.to_ref() for chat in federation.chats]))

        subscribing_feds = await Federation.find(Federation.subscribed == federation.fed_id).to_list()
        for sub_fed in subscribing_feds:
            if sub_fed.chats:
                chat_iids.update(FederationService._normalize_chat_iids([chat.to_ref() for chat in sub_fed.chats]))

        if not chat_iids:
            return 0

        chats = await ChatModel.find(In(ChatModel.iid, list(chat_iids))).to_list()

        unbanned_count = 0
        for chat in chats:
            if await restrict_unban_user(chat.tid, user_tid):
                unbanned_count += 1

        return unbanned_count

    @staticmethod
    async def unban_user_in_chat_iids(chat_iids: list[object], user_tid: int) -> int:
        """Unban user in the provided chat IDs or DBRefs."""
        normalized_chat_iids = FederationService._normalize_chat_iids(chat_iids)
        if not normalized_chat_iids:
            return 0

        chats = await ChatModel.find(In(ChatModel.iid, normalized_chat_iids)).to_list()

        unbanned_count = 0
        for chat in chats:
            if await restrict_unban_user(chat.tid, user_tid):
                unbanned_count += 1

        return unbanned_count

    @staticmethod
    async def get_federation_bans(fed_id: str) -> List[FederationBan]:
        """Get all bans in a federation."""
        return await FederationBan.find(FederationBan.fed_id == fed_id).to_list()

    @staticmethod
    async def is_user_banned(fed_id: str, user_tid: int) -> Optional[FederationBan]:
        """Check if user is banned in federation."""
        return await FederationBan.find_one(FederationBan.fed_id == fed_id, FederationBan.user_id == user_tid)

    @staticmethod
    async def validate_ban_eligibility(federation: Federation, target_user_tid: int, banner_user_tid: int) -> None:
        """Validate if a user can be banned in a federation.

        Args:
            federation: The federation object
            target_user_tid: The Telegram user ID to ban
            banner_user_tid: The Telegram user ID performing the ban

        Raises:
            FederationBanValidationError: If the ban is not permitted
        """
        # Cannot ban bot operators
        if target_user_tid in CONFIG.operators:
            raise FederationBanValidationError("Cannot ban bot operators")

        # Cannot ban self
        if target_user_tid == banner_user_tid:
            raise FederationBanValidationError("You cannot ban yourself")

        # Cannot ban bot
        if target_user_tid == CONFIG.bot_id:
            raise FederationBanValidationError("Cannot ban the bot")

        # Get federation creator's Telegram ID for comparison
        creator = await federation.creator.fetch()
        if creator and target_user_tid == creator.tid:
            raise FederationBanValidationError("Cannot ban the federation owner")

        # Cannot ban federation admins
        if federation.admins:
            for admin_link in federation.admins:
                admin = await admin_link.fetch()
                if admin and target_user_tid == admin.tid:
                    raise FederationBanValidationError("Cannot ban federation administrators")

    @staticmethod
    async def _invalidate_export_tasks(fed_id: str) -> None:
        """Cancel pending export tasks when bans change."""
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
    async def _can_user_create_federation(user_iid: PydanticObjectId) -> bool:
        """Check if user can create another federation."""
        user = await ChatModel.get_by_iid(user_iid)
        if not user:
            return False

        # Owners can create unlimited federations
        if user.tid == CONFIG.owner_id:
            return True

        # Count existing federations created by user
        count = await Federation.find(Federation.creator.id == user_iid).count()
        return count < MAX_FEDERATIONS_PER_USER

    @staticmethod
    async def set_federation_log_channel(federation: Federation, chat_iid: PydanticObjectId) -> None:
        """Set the log channel for a federation."""
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return
        federation.log_chat = chat.id
        await federation.save()

    @staticmethod
    async def remove_federation_log_channel(federation: Federation) -> None:
        """Remove the log channel for a federation."""
        federation.log_chat = None
        await federation.save()

    @staticmethod
    async def subscribe_to_federation(federation: Federation, target_fed_id: str) -> bool:
        """Subscribe federation to another federation. Returns True if successful."""
        # Check if target federation exists
        target_fed = await FederationService.get_federation_by_id(target_fed_id)
        if not target_fed:
            return False

        # Check if already subscribed
        if federation.subscribed and target_fed_id in federation.subscribed:
            return False

        # Prevent self-subscription
        if federation.fed_id == target_fed_id:
            return False

        # Add subscription
        if federation.subscribed is None:
            federation.subscribed = []
        federation.subscribed.append(target_fed_id)
        await federation.save()
        return True

    @staticmethod
    async def unsubscribe_from_federation(federation: Federation, target_fed_id: str) -> bool:
        """Unsubscribe federation from another federation. Returns True if successful."""
        if not federation.subscribed or target_fed_id not in federation.subscribed:
            return False

        federation.subscribed.remove(target_fed_id)
        await federation.save()
        return True

    @staticmethod
    async def get_subscription_chain(fed_id: str) -> List[str]:
        """Get all federations in the subscription chain (iterative, avoids recursion depth issues)."""
        chain = []
        to_visit = [fed_id]
        visited = set()

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            fed = await FederationService.get_federation_by_id(current)
            if not fed or not fed.subscribed:
                continue

            for sub_fed_id in fed.subscribed:
                if sub_fed_id not in visited:
                    to_visit.append(sub_fed_id)
                    if sub_fed_id != fed_id:  # Don't add the starting fed
                        chain.append(sub_fed_id)

        return chain

    @staticmethod
    async def is_user_banned_in_chain(fed_id: str, user_tid: int) -> Optional[tuple[FederationBan, Federation]]:
        """Check if user is banned in federation or any subscribed federation."""
        # Check direct ban first
        direct_ban = await FederationService.is_user_banned(fed_id, user_tid)
        if direct_ban:
            fed = await FederationService.get_federation_by_id(fed_id)
            if fed:
                return direct_ban, fed

        # Check subscription chain
        chain = await FederationService.get_subscription_chain(fed_id)
        for sub_fed_id in chain:
            ban = await FederationService.is_user_banned(sub_fed_id, user_tid)
            if ban:
                fed = await FederationService.get_federation_by_id(sub_fed_id)
                if fed:
                    return ban, fed

        return None

    @staticmethod
    async def post_federation_log(federation: Federation, text: str, bot: Bot | None) -> None:
        """Post a log message to the federation's log channel."""
        if not federation.log_chat or not bot:
            return

        if isinstance(federation.log_chat, ChatModel):
            log_chat = federation.log_chat
        else:
            log_chat = await federation.log_chat.fetch()
            if not log_chat:
                return

        try:
            await bot.send_message(log_chat.tid, text)
        except (TelegramBadRequest, TelegramForbiddenError):
            # If we can't send to the log channel, silently ignore
            # Could potentially remove the log channel if it's invalid
            pass

    @staticmethod
    async def promote_admin(federation: Federation, user_iid: PydanticObjectId) -> None:
        """Promote a user to federation admin.

        Args:
            federation: The federation to promote in
            user_iid: The internal ID of the user to promote

        Raises:
            ValueError: If user is already an admin
        """
        # Check if user is already an admin
        for admin_link in federation.admins:
            if admin_link.id == user_iid:
                raise ValueError("User is already an admin")

        # Add user to admins list
        db_ref = DBRef("chats", user_iid)
        federation.admins.append(Link(db_ref, ChatModel))  # type: ignore[arg-type]
        await federation.save()

    @staticmethod
    async def demote_admin(federation: Federation, user_iid: PydanticObjectId) -> None:
        """Demote a user from federation admin.

        Args:
            federation: The federation to demote in
            user_iid: The internal ID of the user to demote

        Raises:
            ValueError: If user is not an admin
        """
        # Find and remove user from admins list
        admin_count = len(federation.admins)
        federation.admins = [admin for admin in federation.admins if admin.id != user_iid]

        if len(federation.admins) == admin_count:
            raise ValueError("User is not an admin")

        await federation.save()

    @staticmethod
    async def is_admin(federation: Federation, user_tid: int) -> bool:
        """Check if a user is a federation admin.

        Args:
            federation: The federation to check
            user_tid: The Telegram ID of the user to check

        Returns:
            True if user is an admin, False otherwise
        """
        # Check creator (owner)
        creator = await federation.creator.fetch()
        if creator and creator.tid == user_tid:
            return True

        # Check admin list
        for admin_link in federation.admins:
            admin = await admin_link.fetch()
            if admin and admin.tid == user_tid:
                return True

        return False
