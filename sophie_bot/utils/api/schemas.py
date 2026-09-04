from __future__ import annotations

from aiogram.types import RichMessage
from pydantic import BaseModel, ConfigDict, Field

from sophie_bot.db.models.notes import NoteFile, SaveableParseMode
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.rich import rich_message_to_html_fallback, validate_rich_message_api


class RestSaveable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str | None = ""
    file: NoteFile | None = None
    files: list[NoteFile] = Field(default_factory=list)
    buttons: list[list[Button]] = Field(default_factory=list)
    parse_mode: SaveableParseMode | None = SaveableParseMode.html
    preview: bool | None = False
    rich_message: RichMessage | None = None
    version: int | None = 1


def validate_rest_rich_payload(model: RestSaveable) -> RestSaveable:
    if model.rich_message is None:
        return model
    validate_rich_message_api(model.rich_message)
    if model.file or model.files:
        raise ValueError("Rich messages cannot be combined with legacy media fields")
    fallback = rich_message_to_html_fallback(model.rich_message)
    if model.text not in (None, "", fallback):
        raise ValueError("text must match the Rich message fallback")
    model.text = fallback
    model.file = None
    model.files = []
    return model
