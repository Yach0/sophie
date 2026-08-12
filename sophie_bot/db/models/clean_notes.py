from __future__ import annotations

from collections.abc import Sequence

from beanie import Document, PydanticObjectId
from pydantic import Field

from sophie_bot.db.models.chat import ChatModel

from ._link_type import Link


class CleanNotesModel(Document):
    """Per-chat state of the automatic notes cleanup (/cleannotes)."""

    chat: Link[ChatModel]

    enabled: bool = False
    # Every message of the last note sent to the chat: an album note is several messages.
    last_msgs: list[int] = Field(default_factory=list)

    class Settings:
        name = "clean_notes"

    @staticmethod
    async def get_by_chat_iid(chat_iid: PydanticObjectId) -> CleanNotesModel:
        return await CleanNotesModel.find_one(CleanNotesModel.chat.id == chat_iid) or CleanNotesModel(chat=chat_iid)

    async def set_status(self, new_state: bool) -> CleanNotesModel:
        self.enabled = new_state
        return await self.save()

    async def new_messages(self, msg_ids: Sequence[int]) -> CleanNotesModel:
        """Replaces the tracked note messages, so only the newest note is kept in the chat."""
        self.last_msgs = list(msg_ids)
        return await self.save()
