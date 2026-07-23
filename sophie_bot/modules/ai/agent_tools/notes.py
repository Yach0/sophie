from __future__ import annotations


from pydantic_ai import ModelRetry, RunContext, Tool

from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.agent_tools._utils.get_chat_notes import AIChatGetNoteFunc, AIChatNote, AIChatNotesFunc
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext


def _normalize_notenames(notename: str) -> tuple[str, ...]:
    normalized_notenames = tuple(
        normalized_name
        for normalized_name in (name.strip().lower().removeprefix("#") for name in notename.split())
        if normalized_name
    )
    if not normalized_notenames:
        raise ModelRetry("The note name must not be empty. Provide a short non-empty note name.")
    return normalized_notenames


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
    normalized_notename = _normalize_notenames(notename)[0]
    async with track_ai_tool("get_note_content"):
        note_func = AIChatGetNoteFunc()
        return await note_func(ctx, normalized_notename)


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
