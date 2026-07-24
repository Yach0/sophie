from datetime import datetime
from typing import Annotated, ClassVar, Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from ._link_type import Link
from .chat import ChatModel


class ChatConnectionModel(Document):
    user: Annotated[Link[ChatModel], Indexed(unique=True)]
    chat: Link[ChatModel] | None = None
    expires_at: datetime | None = None
    history: list[Link[ChatModel]] = Field(default_factory=list)

    class Settings:
        name = "connections"
        indexes: ClassVar = [
            IndexModel(
                [
                    ("user.$id", ASCENDING),
                    ("chat.$id", ASCENDING),
                ],
                unique=True,
                name="user_chat",
            ),
        ]

    @staticmethod
    async def get_by_user_iid(user_iid: PydanticObjectId) -> Optional["ChatConnectionModel"]:
        return await ChatConnectionModel.find_one(ChatConnectionModel.user.id == user_iid)

    @staticmethod
    async def get_by_user_tid(user_tid: int) -> Optional["ChatConnectionModel"]:
        user = await ChatModel.get_by_tid(user_tid)
        if not user:
            return None
        return await ChatConnectionModel.get_by_user_iid(user.iid)
