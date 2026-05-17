from typing import Sequence

from pydantic import TypeAdapter
from pydantic_ai import RunContext
from typing_extensions import TypedDict

from sophie_bot.db.models import NoteModel
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt


class AIChatNote(TypedDict):
    names: Sequence[str]
    title: str | None
    text: str | None


notes_ta = TypeAdapter(list[AIChatNote])


class AIChatNotesFunc:
    @staticmethod
    def from_model(note: NoteModel) -> AIChatNote:
        return AIChatNote(names=note.names, title=note.description, text=note.text)

    async def __call__(self, ctx: RunContext["SophieAIToolContenxt"]) -> list["AIChatNote"]:
        notes = await NoteModel.get_chat_notes(ctx.deps.connection.db_model.iid)
        return notes_ta.validate_python(self.from_model(note) for note in notes)


class AIChatGetNoteFunc:
    async def __call__(self, ctx: RunContext["SophieAIToolContenxt"], notename: str) -> AIChatNote | None:
        note = await NoteModel.get_by_notenames(ctx.deps.connection.db_model.iid, [notename])
        if note is None:
            return None
        return AIChatNotesFunc.from_model(note)
