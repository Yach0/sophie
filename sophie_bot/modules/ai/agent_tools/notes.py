from __future__ import annotations

from datetime import datetime, timezone

from pydantic_ai import ModelRetry, RunContext, Tool

from sophie_bot.db.models.notes import NoteModel, SaveableParseMode
from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.agent_tools._utils.get_chat_notes import AIChatGetNoteFunc, AIChatNote, AIChatNotesFunc
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.utils.i18n import gettext as _


def _normalize_notename(notename: str) -> str:
    normalized_notename = notename.strip().lower().removeprefix("#")
    if not normalized_notename:
        raise ModelRetry("The note name must not be empty. Provide a short non-empty note name.")
    return normalized_notename


async def get_notes(ctx: RunContext[SophieAIToolContext]) -> list[AIChatNote]:
    """Get the list of notes saved in the current chat."""
    async with track_ai_tool("get_notes"):
        note_func = AIChatNotesFunc()
        return await note_func(ctx)


async def get_note_content(ctx: RunContext[SophieAIToolContext], notename: str) -> AIChatNote | None:
    """Get one chat note by name and return its title and content.

    Args:
        notename: The note name or alias to retrieve. A leading # is optional.
    """
    normalized_notename = _normalize_notename(notename)
    async with track_ai_tool("get_note_content"):
        note_func = AIChatGetNoteFunc()
        return await note_func(ctx, normalized_notename)


async def save_note(
    ctx: RunContext[SophieAIToolContext], notename: str, content: str, title: str | None = None
) -> AIChatNote:
    """Save a new chat note for later retrieval by users and AI tools.

    Args:
        notename: Short note name or alias. A leading # is optional.
        content: Markdown content to store in the note.
        title: Optional human-readable title or short description for the note.
    """
    from sophie_bot.modules.utils_.admin import is_user_admin

    if ctx.deps.user_tid is None or not await is_user_admin(ctx.deps.chat_tid, ctx.deps.user_tid):
        raise ModelRetry("Note management requires admin privileges in this chat. I cannot save notes for non-admin users.")

    normalized_notename = _normalize_notename(notename)
    normalized_content = content.strip()
    if not normalized_content:
        raise ModelRetry("The note content must not be empty. Provide the content that should be saved.")

    async with track_ai_tool("save_note"):
        chat = ctx.deps.connection.db_model
        existing_note = await NoteModel.get_by_notenames(ctx.deps.chat_iid, [normalized_notename])
        if existing_note is not None:
            return AIChatNotesFunc.from_model(existing_note)

        note = NoteModel(
            chat_id=ctx.deps.chat_tid,
            chat=chat,
            names=(normalized_notename,),
            text=ai_markdown_to_html(normalized_content),
            parse_mode=SaveableParseMode.html,
            description=title,
            ai_description=title is not None,
            created_date=datetime.now(timezone.utc),
        )
        await note.insert()
        return AIChatNotesFunc.from_model(note)


async def delete_note(ctx: RunContext[SophieAIToolContext], notename: str) -> str:
    """Delete a chat note by name.

    Args:
        notename: The note name or alias to delete. A leading # is optional.
    """
    from sophie_bot.modules.utils_.admin import is_user_admin

    if ctx.deps.user_tid is None or not await is_user_admin(ctx.deps.chat_tid, ctx.deps.user_tid):
        raise ModelRetry("Note management requires admin privileges in this chat. I cannot delete notes for non-admin users.")

    normalized_notename = _normalize_notename(notename)
    async with track_ai_tool("delete_note"):
        note = await NoteModel.get_by_notenames(ctx.deps.chat_iid, (normalized_notename,))
        if note is None:
            raise ModelRetry("No note was found with that name. Use get_notes to inspect available notes first.")

        await note.delete()
        return _("Note was successfully deleted.")


get_notes_tool = Tool(
    get_notes,
    name="get_notes",
    description="Get notes saved in the current chat.",
    takes_ctx=True,
)
get_note_content_tool = Tool(
    get_note_content,
    name="get_note_content",
    description="Get a chat note by notename and return its title and content.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
save_note_tool = Tool(
    save_note,
    name="save_note",
    description=(
        "Save a new chat note. Use only when the user explicitly asks Sophie to create or remember a note. "
        "Returns the existing note instead of overwriting if the notename is already taken."
    ),
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
delete_note_tool = Tool(
    delete_note,
    name="delete_note",
    description="Delete a chat note by notename. Use only when the user explicitly asks Sophie to delete a note.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
