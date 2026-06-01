from __future__ import annotations

from pydantic_ai import RunContext, Tool

from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.research_agent import run_research_workflow


async def research_tool(ctx: RunContext[SophieAIToolContext], topic: str) -> str:
    """Run a multi-stage research workflow on a topic.

    Use this when the user asks for deep research, a comprehensive analysis,
    or a detailed investigation of a topic. This searches the web, analyzes
    findings, and produces a structured report.

    Args:
        topic: The research topic or question to investigate.
    """
    async with track_ai_tool("research"):
        report = await run_research_workflow(
            topic=topic,
            chat_tid=ctx.deps.chat_tid,
            chat_iid=ctx.deps.chat_iid,
            connection=ctx.deps.connection,
        )
        parts = [f"# {report.title}\n", report.summary, ""]
        for section in report.sections:
            parts.append(f"## {section['heading']}")
            parts.append(section["body"])
            parts.append("")
        if report.sources:
            parts.append("## Sources")
            for source in report.sources:
                parts.append(f"- [{source['title']}]({source['url']})")
        return "\n".join(parts)


research_ai_tool = Tool(
    research_tool,
    name="research",
    description="Run a multi-stage research workflow that searches the web, analyzes findings, and produces a structured report.",
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
