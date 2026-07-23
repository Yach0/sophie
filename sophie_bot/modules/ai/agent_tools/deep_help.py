from __future__ import annotations

from pydantic_ai import RunContext, Tool

from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.deep_help import run_deep_help


async def deep_help(ctx: RunContext[SophieAIToolContext], question: str) -> str:
    """Find out how Sophie behaves by inspecting its own source code.

    Args:
        question: One specific question about Sophie's behaviour, in full, as the user asked it.
    """
    async with track_ai_tool("deep_help"):
        return await run_deep_help(question, ctx.deps.chat_iid, ctx.deps.chat_tid)


deep_help_tool = Tool(
    deep_help,
    name="deep_help",
    description=(
        "Last resort for questions about how Sophie itself behaves: starts a sub-agent that reads "
        "Sophie's source code and reports back briefly. Expensive and rate limited, so only use it "
        "after the `help` tool and its wiki pages failed to answer, and never for anything other "
        "than Sophie's own behaviour. Explain its answer to the user in your own words."
    ),
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
