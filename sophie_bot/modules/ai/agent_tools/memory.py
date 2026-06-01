from __future__ import annotations

from pydantic_ai import ModelRetry, RunContext, Tool

from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext


async def write_memory(ctx: RunContext[SophieAIToolContext], information_to_save: str) -> str:
    """Save information to the chat's long-term memory.

    Args:
        information_to_save: The fact, preference, or instruction to remember for this chat.
    """
    normalized_information = information_to_save.strip()
    if not normalized_information:
        raise ModelRetry("The memory text must not be empty. Provide the information that should be remembered.")

    async with track_ai_tool("write_memory"):
        await AIMemoryModel.append_line(ctx.deps.connection.db_model, normalized_information)

    return "Saved to memory."


async def forget_memory(ctx: RunContext[SophieAIToolContext], index: int) -> str:
    """Forget one long-term memory item by its visible 1-based index.

    Args:
        index: The 1-based memory index shown in the instructions.
    """
    if index < 1:
        raise ModelRetry("Memory indexes start at 1. Provide a valid memory index from the memory list.")

    async with track_ai_tool("forget_memory"):
        removed = await AIMemoryModel.remove_line_by_index(ctx.deps.chat_iid, index - 1)

    if not removed:
        raise ModelRetry("No memory exists at that index. Use an index from the current memory list.")

    return "Memory forgotten."


write_memory_tool = Tool(
    write_memory,
    name="write_memory",
    description="Save information to the chat's long-term memory.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
forget_memory_tool = Tool(
    forget_memory,
    name="forget_memory",
    description="Forget information from the chat's long-term memory by index.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
