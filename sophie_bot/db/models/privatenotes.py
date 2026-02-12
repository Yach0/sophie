from __future__ import annotations

from beanie import Document, PydanticObjectId
from ._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class PrivateNotesModel(Document):
    chat: Link[ChatModel]

    class Settings:
        name = "privatenotes"

    @staticmethod
    async def get_state(chat_iid: PydanticObjectId) -> bool:
        return bool(await PrivateNotesModel.find_one(PrivateNotesModel.chat.id == chat_iid))

    @staticmethod
    async def set_state(chat_iid: PydanticObjectId, new_state: bool):
        model = await PrivateNotesModel.find_one(PrivateNotesModel.chat.id == chat_iid)
        if model and not new_state:
            return await model.delete()

        elif model:
            return model

        model = PrivateNotesModel(chat=chat_iid)
        return await model.save()
