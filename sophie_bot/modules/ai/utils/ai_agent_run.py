from __future__ import annotations

from typing import Any

from pydantic_ai import Agent

from sophie_bot.modules.ai.utils.ai_errors import AIRetryCallback
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult, run_ai_structured


async def ai_agent_run(
    agent: Agent[Any, Any],
    on_retry: AIRetryCallback | None = None,
    **kwargs: Any,
) -> AIAgentResult[Any]:
    return await run_ai_structured(agent, on_retry=on_retry, **kwargs)


__all__ = ("AIAgentResult", "ai_agent_run")
