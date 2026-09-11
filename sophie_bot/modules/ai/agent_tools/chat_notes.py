from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from sophie_bot.db.models import NoteModel
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext


class AIChatNote(BaseModel):
    names: tuple[str, ...] = Field(description="Names and aliases that identify the note.")
    title: str | None = Field(default=None, description="Human-readable note title or short description.")
    text: str | None = Field(default=None, description="Stored note content when available.")


class AIChatNotesFunc:
    @staticmethod
    def from_model(note: NoteModel) -> AIChatNote:
        return AIChatNote(names=tuple(note.names), title=note.description, text=note.text)

    async def __call__(self, ctx: RunContext[SophieAIToolContext]) -> list[AIChatNote]:
        notes = await NoteModel.get_chat_notes(ctx.deps.chat_iid)
        return [self.from_model(note) for note in notes]


class AIChatGetNoteFunc:
    async def __call__(self, ctx: RunContext[SophieAIToolContext], notename: str) -> AIChatNote | None:
        note = await NoteModel.get_by_notenames(ctx.deps.chat_iid, [notename])
        if note is None:
            return None
        return AIChatNotesFunc.from_model(note)
