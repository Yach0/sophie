from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.services.telegram_media import TelegramMediaService
from sophie_bot.utils.api.auth import rest_require_admin
from sophie_bot.utils.api.dependencies import get_chat_or_404

from .schemas import NoteResponse, NotesListResponse

router = APIRouter()


@router.get("/{chat_iid}", response_model=NotesListResponse)
async def list_notes(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    user: Annotated[ChatModel, Depends(rest_require_admin())],
) -> NotesListResponse:
    notes = await NoteModel.get_chat_notes(chat.iid)

    texts = [note.text for note in notes]
    media_result = await TelegramMediaService.resolve_media_from_texts(texts)

    note_responses = [NoteResponse.from_model(note, media_result.resolved) for note in notes]

    return NotesListResponse(notes=note_responses, resolved_media=media_result.resolved)


@router.get("/{chat_iid}/{note_id}", response_model=NoteResponse)
async def get_note(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    note_id: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin())],
) -> NoteResponse:
    note = await NoteModel.get(note_id)
    if not note or (note.chat and note.chat.ref.id != chat.iid) or (not note.chat and note.chat_tid != chat.tid):
        raise HTTPException(status_code=404, detail="Note not found")

    media_result = await TelegramMediaService.resolve_media_from_texts([note.text])

    return NoteResponse.from_model(note, media_result.resolved)
