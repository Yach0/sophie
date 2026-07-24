from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from sophie_bot.db.models.notes_buttons import Button


class RestSaveable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str | None = ""
    buttons: list[list[Button]] = []
    preview: bool | None = False
