from __future__ import annotations

from fastapi import APIRouter

from sophie_bot.db.models.rules import RulesModel
from sophie_bot.utils.api.dependencies import ChatDep, ReadAdminDep

from .schemas import RulesResponse

router = APIRouter()


@router.get("/{chat_iid}", response_model=RulesResponse)
async def get_rules(
    chat: ChatDep,
    user: ReadAdminDep,
):
    if rules := await RulesModel.get_rules(chat.iid):
        return RulesResponse.model_validate(rules)
    return RulesResponse()
