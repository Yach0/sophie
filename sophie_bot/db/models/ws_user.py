from datetime import UTC, datetime
from typing import Any, Literal, Optional

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
    transition: Literal["completing", "expiring", "exempting"] | None = None
    is_join_request: bool = False
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    membership_id: PydanticObjectId | None = None
    membership_join_message_id: int | None = None

    class Settings:
        name = "ws_users"

    @staticmethod
    async def ensure_user(user: "ChatModel", group: "ChatModel", is_join_request: bool) -> "WSUserModel":
        membership = await UserInGroupModel.get_user_in_group(user.iid, group.iid)
        membership_id = membership.id if membership is not None else None
        joined_message_id = membership.joined_message_id if membership is not None else None
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
                membership_join_message_id=joined_message_id,
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

        if membership_id is not None:
            # Rejoins can reuse the membership row while an older leave is still delayed.
            session_filter: dict[str, Any]
            if joined_message_id is None:
                session_filter = {"membership_id": {"$ne": membership_id}}
            else:
                session_filter = {
                    "$or": [
                        {"membership_join_message_id": {"$lt": joined_message_id}},
                        {"membership_join_message_id": None},
                        {
                            "membership_join_message_id": joined_message_id,
                            "membership_id": {"$ne": membership_id},
                        },
                    ]
                }
            await WSUserModel.find_one(
                user_filter,
                {"passed": False},
                session_filter,
            ).update(
                Set(
                    {
                        WSUserModel.membership_id: membership_id,
                        WSUserModel.membership_join_message_id: joined_message_id,
                        WSUserModel.added_at: datetime.now(UTC),
                        WSUserModel.transition: None,
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
    async def remove_unpassed_user(
        ws_user_iid: PydanticObjectId,
        membership_id: PydanticObjectId,
        joined_message_id: int | None,
    ) -> bool:
        result = await WSUserModel.find_one(
            WSUserModel.id == ws_user_iid,
            {"passed": False},
            {
                "$or": [
                    {
                        "membership_id": membership_id,
                        "membership_join_message_id": joined_message_id,
                    },
                    {"membership_id": None, "membership_join_message_id": None},
                ]
            },
        ).delete()
        return result is not None and result.deleted_count > 0

    @staticmethod
    async def is_user(user_iid: PydanticObjectId, group_iid: PydanticObjectId) -> Optional["WSUserModel"]:
        return await WSUserModel.find_one(WSUserModel.user.id == user_iid, WSUserModel.group.id == group_iid)

    def session_filter(self) -> dict[str, object]:
        return {
            "_id": self.id,
            "passed": False,
            "added_at": self.added_at,
            "membership_id": self.membership_id,
            "membership_join_message_id": self.membership_join_message_id,
        }

    async def claim_transition(
        self, transition: Literal["completing", "expiring", "exempting"]
    ) -> "WSUserModel | None":
        """Choose the durable outcome once; Telegram execution is retryable, not atomic."""
        return await WSUserModel.find_one(self.session_filter(), {"transition": None}).update(
            Set({WSUserModel.transition: transition}), response_type=UpdateResponse.NEW_DOCUMENT
        )

    async def transition_is_current(self) -> bool:
        return await WSUserModel.find_one(self.session_filter(), {"transition": self.transition}) is not None

    async def finish_transition(self) -> bool:
        if self.transition is None:
            return False
        result = await WSUserModel.find_one(self.session_filter(), {"transition": self.transition}).delete()
        return result is not None and result.deleted_count > 0
