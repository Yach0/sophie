from __future__ import annotations

from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from sophie_bot.metrics import count_retries_from_messages, track_ai_agent_result, track_ai_request, track_ai_usage
from sophie_bot.utils.exception import SophieException

AgentDepsT = TypeVar("AgentDepsT")
OutputT = TypeVar("OutputT")


class AIAgentResult(BaseModel, Generic[OutputT]):
    output: OutputT
    steps: int | None = None
    retries: int | None = None
    message_history: list[ModelRequest | ModelResponse]
    usage: RunUsage


async def ai_agent_run(agent: Agent[AgentDepsT, OutputT], **kwargs: Any) -> AIAgentResult[OutputT]:
    model = agent.model
    if model is None:
        raise ValueError("Agent model cannot be None for metrics tracking")
    if not isinstance(model, Model):
        raise ValueError(f"Agent model must be a Model instance, got {type(model)}")

    async with track_ai_request(model, "agent"):
        try:
            result = await agent.run(**kwargs)
        except UnexpectedModelBehavior as error:
            raise SophieException("AI provider returned an invalid response. Please try again later.") from error
        except UsageLimitExceeded as error:
            raise SophieException(
                "AI request exceeded the configured usage limits. Please try a shorter request."
            ) from error
        except ModelHTTPError as error:
            raise SophieException(f"AI model error: {error.message}") from error

    message_history = cast(list[ModelRequest | ModelResponse], result.all_messages())
    retries = count_retries_from_messages(message_history)

    if result.usage:
        track_ai_usage(model, result.usage)
        track_ai_agent_result(
            model,
            result.usage,
            message_history,
            output_length=len(str(result.output)),
            retries=retries,
        )

    return AIAgentResult(
        output=result.output,
        retries=retries,
        message_history=message_history,
        usage=result.usage,
    )
