from collections.abc import Awaitable, Callable
from typing import Any, Optional, TypeVar

from aiogram.types import Message, ReplyKeyboardMarkup
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import Model

from sophie_bot.metrics import track_ai_request
from sophie_bot.modules.ai.utils.ai_agent_run import AIAgentResult, ai_agent_run
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.utils.exception import SophieException

RESPONSE_TYPE = TypeVar("RESPONSE_TYPE", bound=BaseModel)
TextStreamCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str], Awaitable[None]]


async def _notify_tool_calls(result_stream: Any, on_tool_call: ToolCallCallback | None) -> None:
    if on_tool_call is None:
        return

    for part in result_stream.response.parts:
        if isinstance(part, ToolCallPart):
            await on_tool_call(part.tool_name)


def _inject_request_options(
    kwargs: dict,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
) -> None:
    if user_tracking_id is None and session_id is None and service_tier is None:
        return
    model_settings = dict(kwargs.get("model_settings") or {})
    extra_body = dict(model_settings.get("extra_body") or {})
    if user_tracking_id is not None:
        extra_body["user"] = str(user_tracking_id)
    if session_id is not None:
        extra_body["session_id"] = session_id
    if service_tier is not None:
        extra_body["service_tier"] = service_tier
    model_settings["extra_body"] = extra_body
    kwargs["model_settings"] = model_settings


async def new_ai_generate(
    history: NewAIMessageHistory,
    model: Model,
    agent_kwargs=None,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    **kwargs,
) -> AIAgentResult:
    """
    Used to generate the AI Chat-bot result text
    """
    if agent_kwargs is None:
        agent_kwargs = {}

    kwargs = dict(kwargs)
    _inject_request_options(kwargs, user_tracking_id, session_id, service_tier)

    agent = Agent(model, **kwargs)
    result = await ai_agent_run(
        agent, user_prompt=history.prompt, message_history=history.message_history, **agent_kwargs
    )
    return result


async def new_ai_generate_stream(
    history: NewAIMessageHistory,
    model: Model,
    on_text_stream: TextStreamCallback,
    agent_kwargs=None,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    on_tool_call: ToolCallCallback | None = None,
    **kwargs,
) -> AIAgentResult:
    """
    Generate AI response while streaming cumulative text chunks through a callback.
    """
    if agent_kwargs is None:
        agent_kwargs = {}

    kwargs = dict(kwargs)
    _inject_request_options(kwargs, user_tracking_id, session_id, service_tier)

    agent = Agent(model, **kwargs)
    async with track_ai_request(model, "agent"):
        try:
            async with agent.run_stream(
                user_prompt=history.prompt,
                message_history=history.message_history,
                **agent_kwargs,
            ) as result_stream:
                await _notify_tool_calls(result_stream, on_tool_call)
                accumulated_text = ""
                async for text_delta in result_stream.stream_text(delta=True, debounce_by=0.2):
                    accumulated_text += text_delta
                    await on_text_stream(accumulated_text)

                output_text = await result_stream.get_output()
                usage = result_stream.usage()
                result_message_history = result_stream.all_messages()
        except UnexpectedModelBehavior as error:
            raise SophieException("AI provider returned an invalid response. Please try again later.") from error
        except ModelHTTPError as err:
            raise SophieException(f"AI model error: {err.message}") from err

    return AIAgentResult(
        output=output_text,
        steps=0,
        retries=0,
        message_history=result_message_history,
        usage=usage,
    )


async def new_ai_generate_schema(
    history: NewAIMessageHistory,
    schema: type[RESPONSE_TYPE],
    model: Model,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    **kwargs,
) -> RESPONSE_TYPE:
    """
    Generate AI response with structured schema output
    """
    kwargs = dict(kwargs)
    _inject_request_options(kwargs, user_tracking_id, session_id, service_tier)

    agent = Agent(model, output_type=schema, **kwargs)
    result: AIAgentResult[RESPONSE_TYPE] = await ai_agent_run(
        agent, user_prompt=history.prompt, message_history=history.message_history
    )
    return result.output


async def new_ai_generate_schema_with_result(
    history: NewAIMessageHistory,
    schema: type[RESPONSE_TYPE],
    model: Model,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    **kwargs,
) -> AIAgentResult[RESPONSE_TYPE]:
    """
    Generate AI response with structured schema output and return full result including usage.
    """
    kwargs = dict(kwargs)
    _inject_request_options(kwargs, user_tracking_id, session_id, service_tier)

    agent = Agent(model, output_type=schema, **kwargs)
    return await ai_agent_run(agent, user_prompt=history.prompt, message_history=history.message_history)


async def new_ai_reply(message: Message, markup: Optional[ReplyKeyboardMarkup] = None) -> Message:
    """
    Generate AI reply and send it as a message
    """
    from sophie_bot.db.models import ChatModel
    from sophie_bot.middlewares.connections import ChatConnection
    from sophie_bot.modules.ai.utils.ai_chatbot_reply import ai_chatbot_reply

    chat_db = await ChatModel.get_by_tid(message.chat.id)
    if not chat_db:
        raise ValueError("Chat not found in database")

    connection = ChatConnection(
        type=chat_db.type,
        is_connected=False,
        tid=chat_db.tid,
        title=chat_db.first_name_or_title,
        db_model=chat_db,
    )

    return await ai_chatbot_reply(message, connection, user_text=None, reply_markup=markup)
