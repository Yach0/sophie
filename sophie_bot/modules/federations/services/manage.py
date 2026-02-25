from __future__ import annotations

import uuid
from typing import List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from beanie import PydanticObjectId
from bson import DBRef

from sophie_bot.config import CONFIG
from sophie_bot.constants import MAX_FEDERATION_NAME_LENGTH, MAX_FEDERATIONS_PER_USER
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation, FederationBan
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.federations.exceptions import (
    FederationAlreadyExistsError,
    FederationContextError,
    FederationLimitExceededError,
    FederationNotFoundError,
)
from sophie_bot.modules.federations.utils.cache_service import FederationCacheService
from sophie_bot.utils.i18n import gettext as _


class FederationManageService:
    """Management operations for federations."""

    @staticmethod
    async def create_federation(name: str, creator_iid: PydanticObjectId) -> Federation:
        if len(name) > MAX_FEDERATION_NAME_LENGTH:
            raise FederationLimitExceededError("Federation name too long")

        if not await FederationManageService._can_user_create_federation(creator_iid):
            raise FederationLimitExceededError("Federation creation limit exceeded")

        if await Federation.find_one(Federation.fed_name == name):
            raise FederationAlreadyExistsError("Federation with this name already exists")

        federation = Federation(fed_name=name, fed_id=str(uuid.uuid4()), creator=creator_iid)
        await federation.insert()
        return federation

    @staticmethod
    async def get_federation_by_id(fed_id: str) -> Optional[Federation]:
        return await Federation.find_one(Federation.fed_id == fed_id)

    @staticmethod
    async def get_federation_by_creator(creator_iid: PydanticObjectId) -> Optional[Federation]:
        return await Federation.find_one(Federation.creator.id == creator_iid)

    @staticmethod
    async def get_federations_by_creator(creator_iid: PydanticObjectId) -> list[Federation]:
        return await Federation.find(Federation.creator.id == creator_iid).to_list()

    @staticmethod
    async def get_federation_for_chat(chat_iid: PydanticObjectId) -> Optional[Federation]:
        fed_id = await FederationCacheService.get_fed_id_for_chat(chat_iid)
        if fed_id:
            return await FederationManageService.get_federation_by_id(fed_id)

        federation = await Federation.find_one(Federation.chats == DBRef(ChatModel.Settings.name, chat_iid))
        if federation:
            await FederationCacheService.set_fed_id_for_chat(chat_iid, federation.fed_id)
        return federation

    @staticmethod
    async def get_federation(
        fed_id_arg: str | None,
        connection: ChatConnection | None = None,
        user_id: int | None = None,
    ) -> Federation:
        if fed_id_arg:
            federation = await FederationManageService.get_federation_by_id(fed_id_arg)
            if not federation:
                raise FederationNotFoundError("Federation not found")
            return federation

        if connection and (connection.is_connected or connection.type != ChatType.private):
            federation = await FederationManageService.get_federation_for_chat(connection.db_model.iid)
            if federation:
                return federation
            raise FederationContextError(_("This chat is not in any federation"))

        if user_id:
            user = await ChatModel.get_by_tid(user_id)
            if not user:
                raise FederationContextError(_("User not found in database"))
            user_federations = await FederationManageService.get_federations_by_creator(user.iid)
            if len(user_federations) == 1:
                return user_federations[0]
            elif len(user_federations) > 1:
                raise FederationContextError(_("You have multiple federations"))
            else:
                raise FederationContextError(_("You don't have any federations"))

        raise FederationContextError(_("Could not determine federation"))

    @staticmethod
    async def update_federation(federation: Federation, updates: dict) -> Federation:
        for key, value in updates.items():
            setattr(federation, key, value)
        await federation.save()
        return federation

    @staticmethod
    async def delete_federation(federation: Federation) -> None:
        await FederationBan.find(FederationBan.fed_id == federation.fed_id).delete()
        if federation.chats:
            for chat in federation.chats:
                await FederationCacheService.invalidate_federation_for_chat(chat.to_ref().id)
        await federation.delete()

    @staticmethod
    async def _can_user_create_federation(user_iid: PydanticObjectId) -> bool:
        user = await ChatModel.get_by_iid(user_iid)
        if not user:
            return False
        if user.tid == CONFIG.owner_id:
            return True
        count = await Federation.find(Federation.creator.id == user_iid).count()
        return count < MAX_FEDERATIONS_PER_USER

    @staticmethod
    async def set_federation_log_channel(federation: Federation, chat_iid: PydanticObjectId) -> None:
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return
        federation.log_chat = chat.id
        await federation.save()

    @staticmethod
    async def remove_federation_log_channel(federation: Federation) -> None:
        federation.log_chat = None
        await federation.save()

    @staticmethod
    async def post_federation_log(federation: Federation, text: str, bot: Bot | None) -> None:
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
            pass

    @staticmethod
    async def subscribe_to_federation(federation: Federation, target_fed_id: str) -> bool:
        target_fed = await FederationManageService.get_federation_by_id(target_fed_id)
        if not target_fed:
            return False
        if federation.subscribed and target_fed_id in federation.subscribed:
            return False
        if federation.fed_id == target_fed_id:
            return False
        if federation.subscribed is None:
            federation.subscribed = []
        federation.subscribed.append(target_fed_id)
        await federation.save()
        return True

    @staticmethod
    async def unsubscribe_from_federation(federation: Federation, target_fed_id: str) -> bool:
        if not federation.subscribed or target_fed_id not in federation.subscribed:
            return False
        federation.subscribed.remove(target_fed_id)
        await federation.save()
        return True

    @staticmethod
    async def get_subscription_chain(fed_id: str) -> List[str]:
        chain = []
        to_visit = [fed_id]
        visited = set()

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)
            fed = await FederationManageService.get_federation_by_id(current)
            if not fed or not fed.subscribed:
                continue
            for sub_fed_id in fed.subscribed:
                if sub_fed_id not in visited:
                    to_visit.append(sub_fed_id)
                    if sub_fed_id != fed_id:
                        chain.append(sub_fed_id)
        return chain

    @staticmethod
    async def get_subscribing_federations(fed_id: str) -> List[Federation]:
        """Get federations that subscribe TO the given federation (reverse lookup).

        Returns federations where `subscribed` list contains the given fed_id.
        """
        return await Federation.find(Federation.subscribed == fed_id).to_list()

    @staticmethod
    async def get_subscribed_by_chain(fed_id: str) -> List[Federation]:
        """Get the full chain of federations that subscribe to the given federation.

        This is the reverse of get_subscription_chain - it finds all federations
        that transitively subscribe TO this federation.

        Example: If Fed A subscribes to Fed B, and Fed B subscribes to Fed C,
        then get_subscribed_by_chain("Fed C") returns [Fed B, Fed A].

        Returns list of federations ordered by distance (closest first).
        """
        chain: List[Federation] = []
        to_visit = [fed_id]
        visited = {fed_id}

        while to_visit:
            current = to_visit.pop(0)  # BFS to maintain order
            # Find all federations that subscribe to 'current'
            subscribing_feds = await Federation.find(Federation.subscribed == current).to_list()

            for sub_fed in subscribing_feds:
                if sub_fed.fed_id not in visited:
                    visited.add(sub_fed.fed_id)
                    chain.append(sub_fed)
                    to_visit.append(sub_fed.fed_id)

        return chain
