from __future__ import annotations

from beanie import PydanticObjectId

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.services.manage import FederationManageService
from sophie_bot.modules.federations.utils.cache_service import FederationCacheService


class FederationChatService:
    """Chat operations for federations."""

    @staticmethod
    async def add_chat_to_federation(federation: Federation, chat_iid: PydanticObjectId) -> bool:
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return False

        existing_chat_iids = [chat_link.to_ref().id for chat_link in federation.chats]
        if chat.iid in existing_chat_iids:
            return False

        federation.chats.append(chat)
        await federation.save()
        await FederationCacheService.set_fed_id_for_chat(chat.iid, federation.fed_id)
        await FederationCacheService.incr_chat_count(federation.fed_id, 1)
        return True

    @staticmethod
    async def remove_chat_from_federation(federation: Federation, chat_iid: PydanticObjectId) -> bool:
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return False

        for chat_link in federation.chats:
            if chat_link.to_ref().id != chat_iid:
                continue

            federation.chats.remove(chat_link)
            await federation.save()
            await FederationCacheService.invalidate_federation_for_chat(chat.iid)
            await FederationCacheService.incr_chat_count(federation.fed_id, -1)
            return True

        return False

    @staticmethod
    async def get_federation_chat_count(fed_id: str) -> int:
        cached = await FederationCacheService.get_chat_count(fed_id)
        if cached is not None:
            return cached

        federation = await FederationManageService.get_federation_by_id(fed_id)
        count = len(federation.chats) if federation and federation.chats else 0
        await FederationCacheService.set_chat_count(fed_id, count)
        return count
