from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic_ai import RunContext, Tool

from sophie_bot.db.models.notes import NoteModel, SaveableParseMode
from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.agent_tools._utils.get_chat_notes import AIChatGetNoteFunc, AIChatNote, AIChatNotesFunc
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.utils.i18n import gettext as _


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


class SaveNoteAgentTool:
    @staticmethod
    async def tool_call(
        ctx: RunContext[SophieAIToolContenxt], notename: str, content: str, title: str | None = None
    ) -> AIChatNote:
        """Save a new chat note for later retrieval by users and AI tools."""
        async with track_ai_tool("save_note"):
            normalized_notename = notename.strip().lower().removeprefix("#")
            if not normalized_notename:
                raise ValueError("notename must not be empty")

            chat = ctx.deps.connection.db_model
            existing_note = await NoteModel.get_by_notenames(chat.iid, [normalized_notename])
            if existing_note is not None:
                return AIChatNotesFunc.from_model(existing_note)

            note = NoteModel(
                chat_id=chat.tid,
                chat=chat,
                names=(normalized_notename,),
                text=ai_markdown_to_html(content),
                parse_mode=SaveableParseMode.html,
                description=title,
                ai_description=title is not None,
                created_date=datetime.now(timezone.utc),
            )
            await note.insert()
            return AIChatNotesFunc.from_model(note)

    def __new__(cls) -> Any:
        return Tool(
            cls.tool_call,
            name="save_note",
            description=(
                "Save a new chat note. Use only when the user explicitly asks Sophie to create or remember a note. "
                "Returns the existing note instead of overwriting if the notename is already taken."
            ),
            takes_ctx=True,
        )


class DeleteNoteAgentTool:
    @staticmethod
    async def tool_call(ctx: RunContext[SophieAIToolContenxt], notename: str) -> str:
        """Delete a chat note by notename."""
        async with track_ai_tool("delete_note"):
            normalized_notename = notename.strip().lower().removeprefix("#")
            if not normalized_notename:
                raise ValueError("notename must not be empty")

            note = await NoteModel.get_by_notenames(ctx.deps.connection.db_model.iid, (normalized_notename,))
            if note is None:
                return _("Note was not found.")

            await note.delete()
            return _("Note was successfully deleted.")

    def __new__(cls) -> Any:
        return Tool(
            cls.tool_call,
            name="delete_note",
            description=(
                "Delete a chat note by notename. Use only when the user explicitly asks Sophie to delete a note."
            ),
            takes_ctx=True,
        )


def notes_list_ai_tool() -> Any:
    return NotesListAgentTool()


def note_content_ai_tool() -> Any:
    return NoteContentAgentTool()


def save_note_ai_tool() -> Any:
    return SaveNoteAgentTool()


def delete_note_ai_tool() -> Any:
    return DeleteNoteAgentTool()
