from typing import Optional

from beanie import Document, PydanticObjectId
from pymongo import ASCENDING, IndexModel

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class ChatConnectionSettingsModel(Document):
    chat: Link[ChatModel]
    allow_users_connect: bool = True

    class Settings:
        name = "chat_connection_settings"
        indexes = [
            IndexModel(
                [("chat.$id", ASCENDING)],
                unique=True,
                name="chat_id_index",
            ),
        ]

    @staticmethod
    async def get_by_chat_iid(chat_iid: PydanticObjectId) -> Optional["ChatConnectionSettingsModel"]:
        return await ChatConnectionSettingsModel.find_one(ChatConnectionSettingsModel.chat.id == chat_iid)
