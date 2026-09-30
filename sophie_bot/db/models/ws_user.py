from datetime import UTC, datetime
from typing import Optional

from beanie import Document, PydanticObjectId, UpdateResponse
from beanie.odm.operators.update.general import Set
from pydantic import Field

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import UserInGroupModel


class WSUserModel(Document):
    user: Link["ChatModel"]
    group: Link["ChatModel"]
    passed: bool = False
    is_join_request: bool = False
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    membership_id: PydanticObjectId | None = None

    class Settings:
        name = "ws_users"

    @staticmethod
    async def ensure_user(user: "ChatModel", group: "ChatModel", is_join_request: bool) -> "WSUserModel":
        membership = await UserInGroupModel.get_user_in_group(user.iid, group.iid)
        membership_id = membership.id if membership is not None else None
        user_filter = {
            "user.$id": user.iid,
            "group.$id": group.iid,
        }
        await WSUserModel.find_one(
            WSUserModel.user.id == user.iid,
            WSUserModel.group.id == group.iid,
        ).upsert(
            Set({}),
            on_insert=WSUserModel(
                user=user,
                group=group,
                is_join_request=is_join_request,
                membership_id=membership_id,
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

        if membership_id is not None:
            await WSUserModel.find_one(
                user_filter,
                {"passed": False},
                {"membership_id": {"$ne": membership_id}},
            ).update(
                Set(
                    {
                        WSUserModel.membership_id: membership_id,
                        WSUserModel.added_at: datetime.now(UTC),
                        WSUserModel.is_join_request: is_join_request,
                    }
                )
            )

        return await WSUserModel.find_one(user_filter)

    @staticmethod
    async def remove_user(user_iid: PydanticObjectId, group_iid: PydanticObjectId) -> Optional["WSUserModel"]:
        user_in_chat = await WSUserModel.find_one(WSUserModel.user.id == user_iid, WSUserModel.group.id == group_iid)
        if user_in_chat:
            await user_in_chat.delete()
        return user_in_chat

    @staticmethod
    async def remove_unpassed_user(ws_user_iid: PydanticObjectId, membership_id: PydanticObjectId | None) -> bool:
        deleted_user = await WSUserModel.get_pymongo_collection().find_one_and_delete(
            {
                "_id": ws_user_iid,
                "passed": False,
                "$or": [{"membership_id": membership_id}, {"membership_id": None}],
            }
        )
        return deleted_user is not None

    @staticmethod
    async def is_user(user_iid: PydanticObjectId, group_iid: PydanticObjectId) -> Optional["WSUserModel"]:
        return await WSUserModel.find_one(WSUserModel.user.id == user_iid, WSUserModel.group.id == group_iid)
