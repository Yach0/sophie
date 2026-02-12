from typing import Optional

from beanie import Document, PydanticObjectId, UpdateResponse
from beanie.odm.operators.update.general import Set
from pymongo.results import DeleteResult

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import Saveable


class RulesModel(Saveable, Document):
    chat: Link[ChatModel]

    class Settings:
        name = "rules"

    @staticmethod
    async def get_rules(chat_iid: PydanticObjectId) -> Optional["RulesModel"]:
        return await RulesModel.find_one(RulesModel.chat.id == chat_iid)

    @staticmethod
    async def set_rules(chat_iid: PydanticObjectId, rules: Saveable) -> "RulesModel":
        data = rules.model_dump()
        data["chat"] = chat_iid
        return await RulesModel.find_one(RulesModel.chat.id == chat_iid).upsert(
            Set(data), on_insert=RulesModel(**data), response_type=UpdateResponse.NEW_DOCUMENT
        )

    @staticmethod
    async def del_rules(chat_iid: PydanticObjectId) -> Optional[DeleteResult]:
        return await RulesModel.find(RulesModel.chat.id == chat_iid).delete()
