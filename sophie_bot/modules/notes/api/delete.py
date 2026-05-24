from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Response, status

from sophie_bot.db.models.notes import NoteModel
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.utils.api.dependencies import ChatDep, ChangeInfoAdminDep

router = APIRouter()


@router.delete("/{chat_iid}/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    chat: ChatDep,
    note_id: PydanticObjectId,
    user: ChangeInfoAdminDep,
) -> Response:
    note = await NoteModel.get(note_id)
    if not note or note.chat_tid != chat.tid:
        raise HTTPException(status_code=404, detail="Note not found")

    note_names = note.names
    await note.delete()
    await log_event(chat.tid, user.tid, LogEvent.NOTE_DELETED, {"note_names": note_names})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
