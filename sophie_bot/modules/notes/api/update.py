from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.utils.api.auth import rest_require_admin
from sophie_bot.utils.api.dependencies import get_chat_or_404

from .schemas import NoteResponse, NoteUpdate

router = APIRouter()


@router.patch("/{chat_iid}/{note_id}", response_model=NoteResponse)
async def update_note(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    note_id: PydanticObjectId,
    note_data: NoteUpdate,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
) -> NoteResponse:
    note = await NoteModel.get(note_id)
    if not note or note.chat_tid != chat.tid:
        raise HTTPException(status_code=404, detail="Note not found")

    update_dict = note_data.model_dump(exclude_unset=True)

    if "names" in update_dict:
        if not update_dict["names"]:
            raise HTTPException(status_code=400, detail="Note names cannot be empty")

        existing = await NoteModel.get_by_notenames(chat.iid, update_dict["names"])
        if existing and existing.id != note.id:
            raise HTTPException(status_code=400, detail=f"One of the names is already taken: {existing.names}")

    for key, value in update_dict.items():
        setattr(note, key, value)

    note.edited_date = datetime.now(timezone.utc)
    note.edited_user = user
    await note.save()
    await log_event(chat.tid, user.tid, LogEvent.NOTE_UPDATED, {"note_names": note.names})

    if note.id is None:
        raise HTTPException(status_code=500, detail="Note ID is missing after save")

    return NoteResponse.from_model(note)
