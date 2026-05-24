from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from sophie_bot.db.models.notes import NoteModel
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.utils.api.dependencies import ChatDep, ChangeInfoAdminDep

from .schemas import NoteCreate, NoteResponse

router = APIRouter()


@router.post("/{chat_iid}", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(
    chat: ChatDep,
    note_data: NoteCreate,
    user: ChangeInfoAdminDep,
) -> NoteResponse:
    if not note_data.names:
        raise HTTPException(status_code=400, detail="Note names cannot be empty")

    existing = await NoteModel.get_by_notenames(chat.iid, note_data.names)
    if existing:
        raise HTTPException(status_code=400, detail=f"One of the names is already taken: {existing.names}")

    note = NoteModel(
        chat_id=chat.tid,
        chat=chat,
        names=note_data.names,
        text=note_data.text,
        file=note_data.file,
        buttons=note_data.buttons,
        parse_mode=note_data.parse_mode,
        preview=note_data.preview,
        description=note_data.description,
        ai_description=note_data.ai_description,
        note_group=note_data.note_group,
        created_date=datetime.now(timezone.utc),
        created_user=user,
    )
    await note.insert()
    await log_event(chat.tid, user.tid, LogEvent.NOTE_SAVED, {"note_names": note.names})

    if not note.id:
        raise HTTPException(status_code=500, detail="Note ID is missing after save")

    return NoteResponse.from_model(note)
