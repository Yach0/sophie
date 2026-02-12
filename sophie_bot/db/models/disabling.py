from typing import Optional

from beanie import Document, PydanticObjectId, UpdateResponse
from beanie.odm.operators.find.comparison import In
from beanie.odm.operators.update.array import AddToSet, Pull

from sophie_bot.db.db_exceptions import DBNotFoundException
from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class DisablingModel(Document):
    chat: Link[ChatModel]

    cmds: list[str]

    class Settings:
        name = "disabled"

    @staticmethod
    async def get_disabled(chat_iid: PydanticObjectId) -> list[str]:
        disabled = await DisablingModel.find_one(DisablingModel.chat.id == chat_iid)

        if not disabled:
            return []

        return disabled.cmds

    @staticmethod
    async def disable(chat_iid: PydanticObjectId, cmd: str) -> "DisablingModel":
        return await DisablingModel.find_one(DisablingModel.chat.id == chat_iid).upsert(
            AddToSet({DisablingModel.cmds: cmd}),
            on_insert=DisablingModel(chat=chat_iid, cmds=[cmd]),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    @staticmethod
    async def enable(chat_iid: PydanticObjectId, cmd: str) -> "DisablingModel":
        model = await DisablingModel.find_one(DisablingModel.chat.id == chat_iid, In(DisablingModel.cmds, [cmd]))

        if not model:
            raise DBNotFoundException()

        return await model.update(Pull({DisablingModel.cmds: cmd}))

    @staticmethod
    async def enable_all(chat_iid: PydanticObjectId) -> Optional["DisablingModel"]:
        model = await DisablingModel.find_one(DisablingModel.chat.id == chat_iid)

        if model:
            await model.delete()

        return model
