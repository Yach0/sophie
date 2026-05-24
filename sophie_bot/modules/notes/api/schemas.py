from __future__ import annotations

from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from sophie_bot.db.models.notes import NoteFile, NoteModel, SaveableParseMode
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.services.telegram_media import ResolvedMedia
from sophie_bot.utils.api.schemas import RestSaveable


class NoteResponse(RestSaveable):
    id: PydanticObjectId | None = None
    names: tuple[str, ...]
    file: NoteFile | None
    parse_mode: SaveableParseMode | None
    description: str | None
    ai_description: bool
    note_group: str | None
    created_date: datetime | None
    edited_date: datetime | None
    resolved_media: dict[str, ResolvedMedia] = Field(
        default_factory=dict,
        description="Resolved Telegram custom emoji and sticker metadata",
    )

    @classmethod
    def from_model(cls, note: NoteModel, resolved_media: dict[str, ResolvedMedia] | None = None) -> NoteResponse:
        return cls(
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
            resolved_media=resolved_media or {},
        )


class NotesListResponse(BaseModel):
    notes: list[NoteResponse]
    resolved_media: dict[str, ResolvedMedia] = Field(
        default_factory=dict,
        description="Resolved Telegram custom emoji and sticker metadata for all notes",
    )


class NoteCreate(RestSaveable):
    names: tuple[str, ...]
    file: NoteFile | None = None
    parse_mode: SaveableParseMode = SaveableParseMode.html
    description: str | None = None
    ai_description: bool = False
    note_group: str | None = None


class NoteUpdate(BaseModel):
    names: tuple[str, ...] | None = None
    text: str | None = None
    file: NoteFile | None = None
    buttons: list[list[Button]] | None = None
    parse_mode: SaveableParseMode | None = None
    preview: bool | None = None
    description: str | None = None
    ai_description: bool | None = None
    note_group: str | None = None


class PMNotesStateUpdate(BaseModel):
    enabled: bool


class PMNotesStateResponse(BaseModel):
    enabled: bool
