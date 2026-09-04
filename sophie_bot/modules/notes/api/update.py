from __future__ import annotations

from datetime import UTC, datetime

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException

from sophie_bot.db.models.notes import CURRENT_SAVEABLE_VERSION, NoteModel, normalize_notenames
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.notes.utils.rich import rich_message_to_html_fallback, validate_rich_message_api
from sophie_bot.utils.api.dependencies import ChangeInfoAdminDep, ChatDep

from .schemas import NoteResponse, NoteUpdate

router = APIRouter()


@router.patch("/{chat_iid}/{note_id}", response_model=NoteResponse)
async def update_note(
    chat: ChatDep,
    note_id: PydanticObjectId,
    note_data: NoteUpdate,
    user: ChangeInfoAdminDep,
) -> NoteResponse:
    note = await NoteModel.get(note_id)
    if not note or note.chat_tid != chat.tid:
        raise HTTPException(status_code=404, detail="Note not found")

    update_dict = note_data.model_dump(exclude_unset=True)

    try:
        if "rich_message" in update_dict and update_dict["rich_message"] is not None:
            rich_message = update_dict["rich_message"]
            validate_rich_message_api(rich_message)
            fallback = rich_message_to_html_fallback(rich_message)
            if "text" in update_dict and update_dict["text"] not in (None, "", fallback):
                raise ValueError("text must match the Rich message fallback")
            if update_dict.get("file") or update_dict.get("files"):
                raise ValueError("Rich messages cannot be combined with legacy media fields")
            update_dict["text"] = fallback
            update_dict["version"] = CURRENT_SAVEABLE_VERSION
            update_dict["file"] = None
            update_dict["files"] = []
        elif note.rich_message is not None and "rich_message" not in update_dict:
            changed_content = {"text", "file", "files", "buttons", "parse_mode"} & update_dict.keys()
            if changed_content:
                raise ValueError("Clear Rich content before changing its ordinary fields")
        if note.rich_message is not None and update_dict.get("rich_message") is None and "rich_message" in update_dict:
            if not update_dict.get("text"):
                raise ValueError("Provide replacement text when clearing Rich content")
            if update_dict.get("file") or update_dict.get("files"):
                raise ValueError("Rich content can only be cleared to text without media")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if "names" in update_dict:
        if not update_dict["names"]:
            raise HTTPException(status_code=400, detail="Note names cannot be empty")

        update_dict["names"] = normalize_notenames(update_dict["names"])

        existing = await NoteModel.get_by_notenames(chat.iid, update_dict["names"])
        if existing and existing.id != note.id:
            raise HTTPException(status_code=400, detail=f"One of the names is already taken: {existing.names}")

    for key, value in update_dict.items():
        setattr(note, key, value)

    note.edited_date = datetime.now(UTC)
    note.edited_user = user
    await note.save()
    await log_event(chat.tid, user.tid, LogEvent.NOTE_UPDATED, {"note_names": note.names})

    if note.id is None:
        raise HTTPException(status_code=500, detail="Note ID is missing after save")

    return NoteResponse.from_model(note)
