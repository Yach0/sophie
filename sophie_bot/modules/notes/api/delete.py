from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Response, status

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.utils.api.auth import rest_require_admin

router = APIRouter()


@router.delete("/{chat_iid}/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    chat_iid: PydanticObjectId,
    note_id: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
) -> Response:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    note = await NoteModel.get(note_id)
    if not note or note.chat_tid != chat.tid:
        raise HTTPException(status_code=404, detail="Note not found")

    note_names = note.names
    await note.delete()
    await log_event(chat.tid, user.tid, LogEvent.NOTE_DELETED, {"note_names": note_names})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
