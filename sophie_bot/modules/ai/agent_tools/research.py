from __future__ import annotations

from pydantic_ai import RunContext, Tool

from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.json_schemas.research import ResearchFinalResponse
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.research import run_research_workflow_response


async def research_topic(ctx: RunContext[SophieAIToolContext], topic: str) -> ResearchFinalResponse:
    """Run multistage web research and return a summary with sources.

    Args:
        topic: Topic or question to research.
    """
    async with track_ai_tool("research_topic"):
        return await run_research_workflow_response(
            topic,
            ctx.deps.connection,
            progress_callback=ctx.deps.research_progress_callback,
        )


research_topic_tool = Tool(
    research_topic,
    name="research_topic",
    description="Run multistage web research and return a summary with sources.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
