from __future__ import annotations

from fastapi import HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.utils_.admin import get_admin_record


async def verify_admin(chat: ChatModel, user: ChatModel) -> ChatModel:
    admin = await get_admin_record(chat, user)
    if not admin:
        raise HTTPException(status_code=403, detail="You are not an admin in this chat")

    return chat
