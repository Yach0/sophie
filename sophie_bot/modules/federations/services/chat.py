from __future__ import annotations

from beanie import PydanticObjectId

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.utils.cache_service import FederationCacheService
from sophie_bot.modules.federations.services.manage import FederationManageService


class FederationChatService:
    """Chat operations for federations."""

    @staticmethod
    async def add_chat_to_federation(federation: Federation, chat_iid: PydanticObjectId) -> None:
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return
        if chat.iid not in [c.to_ref() for c in federation.chats]:
            federation.chats.append(chat)
            await federation.save()
            await FederationCacheService.set_fed_id_for_chat(chat.iid, federation.fed_id)

    @staticmethod
    async def remove_chat_from_federation(federation: Federation, chat_iid: PydanticObjectId) -> None:
        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return
        for c in federation.chats:
            if c.to_ref() == chat_iid:
                federation.chats.remove(c)
                await federation.save()
                await FederationCacheService.invalidate_federation_for_chat(chat.iid)
                break

    @staticmethod
    async def get_federation_chat_count(fed_id: str) -> int:
        federation = await FederationManageService.get_federation_by_id(fed_id)
        if not federation or not federation.chats:
            return 0
        return len(federation.chats)
