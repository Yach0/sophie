from __future__ import annotations

from fastapi import APIRouter

from sophie_bot.db.models.notes import Saveable
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.utils.api.dependencies import ChangeInfoAdminDep, ChatDep

from .schemas import RulesPayload, RulesResponse

router = APIRouter()


@router.put("/{chat_iid}", response_model=RulesResponse)
async def set_rules(
    chat: ChatDep,
    payload: RulesPayload,
    user: ChangeInfoAdminDep,
):
    if not payload.text and not payload.buttons:
        await RulesModel.del_rules(chat.iid)
        return RulesResponse()

    saveable = Saveable(text=payload.text or "", buttons=payload.buttons, preview=payload.preview)
    rules = await RulesModel.set_rules(chat.iid, saveable)
    return RulesResponse.model_validate(rules)
