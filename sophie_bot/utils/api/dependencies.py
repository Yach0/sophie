from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.utils.api.auth import rest_require_admin


async def get_chat_or_404(chat_iid: PydanticObjectId) -> ChatModel:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


ChatDep = Annotated[ChatModel, Depends(get_chat_or_404)]
ReadAdminDep = Annotated[ChatModel, Depends(rest_require_admin())]
ChangeInfoAdminDep = Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))]
RestrictAdminDep = Annotated[ChatModel, Depends(rest_require_admin(permission="can_restrict_members"))]
OwnerAdminDep = Annotated[ChatModel, Depends(rest_require_admin(require_owner=True))]
