from datetime import UTC, datetime
from typing import TYPE_CHECKING

from beanie import Document
from bson import ObjectId
from pydantic import Field

from ._link_type import Link

if TYPE_CHECKING:
    from sophie_bot.db.models import ChatModel


class ChatPhotoModel(Document):
    chat: Link["ChatModel"]
    url: str
    last_updated: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    class Settings:
        name = "chat_photo"

    @staticmethod
    async def upsert_photo(chat_iid: ObjectId, url: str):
        photo = await ChatPhotoModel.find_one({"chat": chat_iid})
        if photo:
            photo.url = url
            photo.last_updated = datetime.now(tz=UTC)
            await photo.save()
        else:
            await ChatPhotoModel(chat=chat_iid, url=url).insert()
