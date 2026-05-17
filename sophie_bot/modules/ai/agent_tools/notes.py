from typing import Any

from pydantic_ai import RunContext, Tool

from sophie_bot.db.models import NoteModel
from sophie_bot.modules.ai.agent_tools._utils.get_chat_notes import AIChatGetNoteFunc, AIChatNote, AIChatNotesFunc
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt


class NotesListAgentTool:
    @staticmethod
    async def tool_call(ctx: RunContext[SophieAIToolContenxt]) -> list[AIChatNote]:
        notes = await NoteModel.get_chat_notes(ctx.deps.connection.db_model.iid)
        return [AIChatNotesFunc.from_model(note) for note in notes]

    def __new__(cls) -> Any:
        return Tool(cls.tool_call, name="get_notes", description="Get notes of the chat", takes_ctx=True)


class NoteContentAgentTool:
    @staticmethod
    async def tool_call(ctx: RunContext[SophieAIToolContenxt], notename: str) -> AIChatNote | None:
        note_func = AIChatGetNoteFunc()
        return await note_func(ctx, notename)

    def __new__(cls) -> Any:
        return Tool(
            cls.tool_call,
            name="get_note_content",
            description="Get a chat note by notename and return its title and content",
            takes_ctx=True,
        )


def notes_list_ai_tool() -> Any:
    return NotesListAgentTool()


def note_content_ai_tool() -> Any:
    return NoteContentAgentTool()
