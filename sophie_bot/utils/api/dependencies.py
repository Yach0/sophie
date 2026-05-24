from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import HTTPException

from sophie_bot.db.models.chat import ChatModel


async def get_chat_or_404(chat_iid: PydanticObjectId) -> ChatModel:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat
