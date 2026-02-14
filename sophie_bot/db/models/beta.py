from asyncio import Lock
from enum import Enum
from typing import Optional

from beanie import Document, PydanticObjectId, UpdateResponse
from beanie.odm.operators.update.general import Set, Unset

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class PreferredMode(Enum):
    auto = 0
    stable = 1
    beta = 2


class CurrentMode(Enum):
    stable = 1
    beta = 2


class BetaModeModel(Document):
    chat: Link[ChatModel]
    preferred_mode: PreferredMode = PreferredMode.auto
    mode: Optional[CurrentMode] = None

    class Settings:
        name = "beta_mode"
        indexes = ["chat.$id"]

    @staticmethod
    async def all_chats_reset_current_mode():
        await BetaModeModel.find(BetaModeModel.mode != None).update(Set({BetaModeModel.mode: None}))  # noqa: E711

    @staticmethod
    async def beta_mode_chats_count():
        return await BetaModeModel.find(BetaModeModel.mode == CurrentMode.beta).count()

    @staticmethod
    async def set_mode(chat_iid: PydanticObjectId, new_mode: CurrentMode) -> "BetaModeModel":
        async with Lock():
            return await BetaModeModel.find_one(BetaModeModel.chat.id == chat_iid).upsert(
                Set({BetaModeModel.mode: new_mode}),
                on_insert=BetaModeModel(
                    chat=chat_iid,
                    mode=new_mode,
                ),
                response_type=UpdateResponse.NEW_DOCUMENT,
            )

    @staticmethod
    async def set_preferred_mode(chat_iid: PydanticObjectId, new_mode: PreferredMode) -> "BetaModeModel":
        return await BetaModeModel.find_one(BetaModeModel.chat.id == chat_iid).upsert(
            Set({BetaModeModel.preferred_mode: new_mode}),
            Unset({BetaModeModel.mode: 1}),
            on_insert=BetaModeModel(
                chat=chat_iid,
                preferred_mode=new_mode,
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    @staticmethod
    async def get_by_chat_iid(chat_iid: PydanticObjectId) -> Optional["BetaModeModel"]:
        return await BetaModeModel.find_one(BetaModeModel.chat.id == chat_iid)
