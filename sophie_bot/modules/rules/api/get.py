from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.utils.api.auth import rest_require_admin
from sophie_bot.utils.api.dependencies import get_chat_or_404

from .schemas import RulesResponse

router = APIRouter()


@router.get("/{chat_iid}", response_model=RulesResponse)
async def get_rules(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    user: Annotated[ChatModel, Depends(rest_require_admin())],
):
    if rules := await RulesModel.get_rules(chat.iid):
        return RulesResponse.model_validate(rules)
    return RulesResponse()
