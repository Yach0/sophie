from __future__ import annotations

from beanie import Document, PydanticObjectId

from sophie_bot.db.models.chat import ChatModel

from ._link_type import Link


class PrivateNotesModel(Document):
    chat: Link[ChatModel]

    class Settings:
        name = "privatenotes"

    @staticmethod
    async def get_state(chat_iid: PydanticObjectId) -> bool:
        return bool(await PrivateNotesModel.find_one(PrivateNotesModel.chat.id == chat_iid))

    @staticmethod
    async def set_state(chat_iid: PydanticObjectId, new_state: bool) -> None:
        if not new_state:
            await PrivateNotesModel.find(PrivateNotesModel.chat.id == chat_iid).delete()
            return

        if await PrivateNotesModel.get_state(chat_iid):
            return

        chat = await ChatModel.get_by_iid(chat_iid)
        if chat is None:
            raise ValueError(f"Chat with internal ID {chat_iid!s} not found")
        await PrivateNotesModel(chat=chat).insert()
