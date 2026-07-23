from asyncio import Lock
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Any, Optional

from aiogram.types import Chat, User
from beanie import (
    DeleteRules,
    Document,
    Indexed,
    PydanticObjectId,
    UpdateResponse,
)
from beanie.odm.operators.find.comparison import In
from beanie.odm.operators.update.general import Set
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from sophie_bot.db.db_exceptions import DBNotFoundException
from sophie_bot.db.models._link_type import Link

upsert_user_lock = Lock()
upsert_group_lock = Lock()


class ChatType(Enum):
    group = "group"
    supergroup = "supergroup"
    private = "private"
    channel = "channel"


class ChatModel(Document):
    iid: PydanticObjectId = Field(default_factory=PydanticObjectId, alias="_id")
    tid: Annotated[int, Indexed(unique=True)] = Field(..., alias="chat_id")
    type: ChatType = Field(..., description="Group type")
    first_name_or_title: str = Field(max_length=128)
    last_name: Optional[str] = Field(max_length=64, default=None)
    username: Annotated[Optional[str], Indexed()]
    language_code: Optional[str] = None
    is_bot: bool
    # Telegram community this chat belongs to (Bot API 10.2). Maintained by
    # SaveChatsMiddleware; never touched by the upsert_group path so it survives updates.
    community_tid: Annotated[Optional[int], Indexed()] = None

    first_saw: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_saw: datetime

    class Settings:
        name = "chats"
        max_nesting_depth = 2

    @staticmethod
    def _get_user_data(user: User) -> dict[str, Any]:
        return {
            "type": ChatType.private,
            "first_name_or_title": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "is_bot": user.is_bot,
            "last_saw": datetime.now(timezone.utc),
            "language_code": user.language_code,
        }

    @staticmethod
    def _get_group_data(chat: Chat) -> dict[str, Any]:
        return {
            "type": ChatType[chat.type],
            "first_name_or_title": chat.title,
            "last_name": None,
            "username": chat.username,
            "is_bot": False,
            "last_saw": datetime.now(timezone.utc),
        }

    @staticmethod
    def get_user_model(user: User) -> "ChatModel":
        return ChatModel(tid=user.id, **ChatModel._get_user_data(user))

    @staticmethod
    def get_group_model(chat: Chat) -> "ChatModel":
        return ChatModel(tid=chat.id, **ChatModel._get_group_data(chat))

    @staticmethod
    def _with_settled_iid(chat: "ChatModel") -> "ChatModel":
        """Make `iid` agree with the `_id` that was actually written.

        `iid` and Beanie's `Document.id` both map to `_id`, but they are separate fields, so
        nothing keeps them equal. When an upsert inserts, Beanie returns the `on_insert`
        template itself rather than re-reading the document: it sets `.id` to the inserted
        `_id` and leaves `.iid` holding the unrelated value its default_factory invented.
        The returned `.iid` then matches no document at all.
        """
        if chat.id is not None:
            chat.iid = chat.id
        return chat

    @staticmethod
    async def upsert_user(user: User) -> "ChatModel":
        async with upsert_user_lock:
            data = ChatModel._get_user_data(user)
            chat = await ChatModel.find_one(ChatModel.tid == user.id).upsert(
                Set(data), on_insert=ChatModel(tid=user.id, **data), response_type=UpdateResponse.NEW_DOCUMENT
            )
            return ChatModel._with_settled_iid(chat)

    @staticmethod
    async def upsert_group(chat: Chat) -> "ChatModel":
        async with upsert_group_lock:
            data = ChatModel._get_group_data(chat)

            group = await ChatModel.find_one(ChatModel.tid == chat.id).upsert(
                Set(data), on_insert=ChatModel(tid=chat.id, **data), response_type=UpdateResponse.NEW_DOCUMENT
            )
            return ChatModel._with_settled_iid(group)

    @staticmethod
    async def do_chat_migrate(old_id: int, new_chat: Chat) -> Optional["ChatModel"]:
        chat = await ChatModel.find_one(ChatModel.tid == old_id)
        if chat:
            chat.tid = new_chat.id
            chat.type = ChatType[new_chat.type]
            await chat.save()
        return chat

    @staticmethod
    async def total_count(chat_types: tuple[ChatType, ...]) -> int:
        return await ChatModel.find(In(ChatModel.type, chat_types)).count()

    @staticmethod
    async def new_count_last_48h(chat_types: tuple[ChatType, ...]) -> int:
        return await ChatModel.find(
            ChatModel.last_saw >= datetime.now(timezone.utc) - timedelta(hours=48),
            ChatModel.first_saw >= datetime.now(timezone.utc) - timedelta(hours=48),
            In(ChatModel.type, chat_types),
        ).count()

    @staticmethod
    async def active_count_last_48h(chat_types: tuple[ChatType, ...]) -> int:
        return await ChatModel.find(
            ChatModel.last_saw >= datetime.now(timezone.utc) - timedelta(hours=48),
            ChatModel.first_saw <= datetime.now(timezone.utc) - timedelta(hours=48),
            In(ChatModel.type, chat_types),
        ).count()

    async def delete_chat(self):
        await self.delete(link_rule=DeleteRules.DELETE_LINKS)

    async def set_community(self, community_tid: int) -> None:
        """Attach this chat to a Telegram community (idempotent, scoped update)."""
        if self.community_tid == community_tid:
            return
        self.community_tid = community_tid
        await ChatModel.find_one(ChatModel.tid == self.tid).update(Set({ChatModel.community_tid: community_tid}))

    async def clear_community(self) -> None:
        """Detach this chat from any Telegram community."""
        if self.community_tid is None:
            return
        self.community_tid = None
        await ChatModel.find_one(ChatModel.tid == self.tid).update(Set({ChatModel.community_tid: None}))

    @staticmethod
    async def get_by_tid(chat_id: int) -> Optional["ChatModel"]:
        return await ChatModel.find_one(ChatModel.tid == chat_id)

    @staticmethod
    async def get_by_iid(iid: PydanticObjectId) -> Optional["ChatModel"]:
        return await ChatModel.find_one(ChatModel.iid == iid)

    @staticmethod
    async def find_user(user_iid: int) -> "ChatModel":
        user = await ChatModel.find_one(ChatModel.tid == user_iid, ChatModel.type == ChatType.private)

        if not user:
            raise DBNotFoundException()

        return user

    @staticmethod
    async def find_user_by_username(username: str):
        user = await ChatModel.find_one(ChatModel.username == username)
        if not user:
            raise DBNotFoundException()

        return user

    @staticmethod
    def user_from_id(user_id: int) -> "ChatModel":
        return ChatModel(
            tid=user_id,
            first_name_or_title="User",
            is_bot=False,  # We don't know, but we can assume
            username=None,
            type=ChatType.private,
            last_saw=datetime.now(timezone.utc),
        )

    @staticmethod
    def export_dict(chat: "ChatModel") -> dict[str, Any]:
        return chat.model_dump(mode="json", exclude_none=True, exclude_unset=True, exclude_defaults=True)


class UserInGroupModel(Document):
    user: Link[ChatModel]
    group: Link[ChatModel]
    first_saw: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_saw: datetime
    ai_filter_seen_messages: int = 0

    class Settings:
        name = "users_in_groups"
        indexes = [
            IndexModel(
                [
                    ("user.$id", ASCENDING),
                    ("group.$id", ASCENDING),
                ],
                unique=True,
                name="user_group_dedup_key",
            ),
        ]

    @staticmethod
    async def ensure_user_in_group(user: "ChatModel", group: "ChatModel"):
        current_timedate = datetime.now(timezone.utc)

        return await UserInGroupModel.find_one({"user.$id": user.iid, "group.$id": group.iid}).upsert(
            Set({UserInGroupModel.last_saw: current_timedate}),
            on_insert=UserInGroupModel(
                user=user,
                group=group,
                last_saw=current_timedate,
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    @staticmethod
    async def remove_user_in_chat(user_iid: PydanticObjectId, group_iid: PydanticObjectId):
        user_in_chat = await UserInGroupModel.find_one({"user.$id": user_iid, "group.$id": group_iid})
        if user_in_chat:
            await user_in_chat.delete()
        return user_in_chat

    @staticmethod
    async def ensure_delete(user: "ChatModel", group: "ChatModel") -> Optional["UserInGroupModel"]:
        if user_in_chat := await UserInGroupModel.find_one({"user.$id": user.iid, "group.$id": group.iid}):
            await user_in_chat.delete()
            return user_in_chat
        return None

    @staticmethod
    async def get_user_in_group(
        user_iid: PydanticObjectId, group_iid: PydanticObjectId
    ) -> Optional["UserInGroupModel"]:
        return await UserInGroupModel.find_one({"user.$id": user_iid, "group.$id": group_iid})


class ChatTopicModel(Document):
    group: Link[ChatModel]
    thread_id: int
    name: Optional[str] = None
    last_active: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "chat_topics"

    @staticmethod
    async def ensure_topic(group: "ChatModel", thread_id: int, topic_name: Optional[str]):
        model: Optional[ChatTopicModel] = await ChatTopicModel.find_one(
            ChatTopicModel.group.id == group.iid, ChatTopicModel.thread_id == thread_id
        )

        if not model:
            model = ChatTopicModel(group=group, thread_id=thread_id, name=topic_name)
            await model.save()
            return model

        if (topic_name and topic_name != model.name) or (topic_name and not model.name):
            model.name = topic_name
            await model.save()

        return model
