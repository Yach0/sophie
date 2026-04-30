from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.services.telegram_media import TelegramMediaService
from sophie_bot.utils.api.auth import rest_require_admin

from .schemas import NoteResponse, NotesListResponse

router = APIRouter()


@router.get("/{chat_iid}", response_model=NotesListResponse)
async def list_notes(
    chat_iid: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin())],
) -> NotesListResponse:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    notes = await NoteModel.get_chat_notes(chat.iid)

    texts = [note.text for note in notes]
    media_result = await TelegramMediaService.resolve_media_from_texts(texts)

    note_responses = [
        NoteResponse(
            id=note.id,
            names=note.names,
            text=note.text,
            file=note.file,
            buttons=note.buttons,
            parse_mode=note.parse_mode,
            preview=note.preview,
            description=note.description,
            ai_description=note.ai_description,
            note_group=note.note_group,
            created_date=note.created_date,
            edited_date=note.edited_date,
            resolved_media=media_result.resolved,
        )
        for note in notes
    ]

    return NotesListResponse(notes=note_responses, resolved_media=media_result.resolved)


@router.get("/{chat_iid}/{note_id}", response_model=NoteResponse)
async def get_note(
    chat_iid: PydanticObjectId,
    note_id: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin())],
) -> NoteResponse:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    note = await NoteModel.get(note_id)
    if not note or (note.chat and note.chat.ref.id != chat.iid) or (not note.chat and note.chat_tid != chat.tid):
        raise HTTPException(status_code=404, detail="Note not found")

    media_result = await TelegramMediaService.resolve_media_from_texts([note.text])

    return NoteResponse(
        id=note.id,
        names=note.names,
        text=note.text,
        file=note.file,
        buttons=note.buttons,
        parse_mode=note.parse_mode,
        preview=note.preview,
        description=note.description,
        ai_description=note.ai_description,
        note_group=note.note_group,
        created_date=note.created_date,
        edited_date=note.edited_date,
        resolved_media=media_result.resolved,
    )
