from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import Saveable
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.utils.api.auth import rest_require_admin
from sophie_bot.utils.api.dependencies import get_chat_or_404

from .schemas import RulesPayload, RulesResponse

router = APIRouter()


@router.put("/{chat_iid}", response_model=RulesResponse)
async def set_rules(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    payload: RulesPayload,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
):
    if not payload.text and not payload.buttons:
        await RulesModel.del_rules(chat.iid)
        return RulesResponse()

    saveable = Saveable(text=payload.text or "", buttons=payload.buttons, preview=payload.preview)
    rules = await RulesModel.set_rules(chat.iid, saveable)
    return RulesResponse.model_validate(rules)
