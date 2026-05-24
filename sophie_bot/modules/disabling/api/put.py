from __future__ import annotations

from beanie.odm.operators.update.general import Set
from fastapi import APIRouter

from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.modules.help.utils.extract_info import DISABLEABLE_CMDS
from sophie_bot.utils.api.dependencies import ChatDep, ChangeInfoAdminDep

from .schemas import DisabledPayload, DisabledResponse

router = APIRouter()


@router.put("/disabled/{chat_iid}", response_model=DisabledResponse)
async def set_disabled_commands(
    chat: ChatDep,
    payload: DisabledPayload,
    user: ChangeInfoAdminDep,
):
    # Filter to only allow disableable commands
    disableable = {cmd.cmds[0] for cmd in DISABLEABLE_CMDS if cmd.cmds}
    to_disable = [cmd for cmd in payload.disabled if cmd in disableable]

    await DisablingModel.find_one(DisablingModel.chat.id == chat.iid).upsert(
        Set({DisablingModel.cmds: to_disable}),
        on_insert=DisablingModel(chat=chat.iid, cmds=to_disable),
    )
    return DisabledResponse(disabled=to_disable)
