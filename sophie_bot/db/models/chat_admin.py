from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import ResultChatMemberUnion
from beanie import Document
from bson import ObjectId
from pydantic import Field, field_validator

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models._link_type import Link


class ChatAdminModel(Document):
    chat: Link[ChatModel]
    user: Link[ChatModel]

    member: ResultChatMemberUnion
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("last_updated", mode="after")
    @classmethod
    def _normalize_last_updated(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    class Settings:
        name = "chat_admin"

    @staticmethod
    async def upsert_admin(chat_iid: ObjectId, user_iid, member: ResultChatMemberUnion):
        admin = await ChatAdminModel.find_one(
            ChatAdminModel.chat.id == chat_iid,
            ChatAdminModel.user.id == user_iid,
        )
        if not admin:
            admin = ChatAdminModel(chat=chat_iid, user=user_iid, member=member)
        else:
            admin.member = member
            admin.last_updated = datetime.now(UTC)
        await admin.save()
        return admin
