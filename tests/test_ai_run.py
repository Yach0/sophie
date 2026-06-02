from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions, build_model_settings, run_ai_structured, run_ai_text


class StructuredOutput(BaseModel):
    value: str


def test_build_model_settings_injects_openai_extra_body() -> None:
    model_settings = build_model_settings(
        {"temperature": 0, "extra_body": {"existing": "kept"}},
        AIRequestOptions(user_tracking_id="chat-iid", session_id="session-id", service_tier="flex"),
    )

    assert model_settings == {
        "temperature": 0,
        "extra_body": {
            "existing": "kept",
            "user": "chat-iid",
            "session_id": "session-id",
            "service_tier": "flex",
        },
    }


async def test_run_ai_text_wraps_output_usage_and_messages() -> None:
    agent = Agent(TestModel(custom_output_text="hello"), output_type=str)

    result = await run_ai_text(agent, "Say hello")

    assert result.output == "hello"
    assert result.usage.requests == 1
    assert result.message_history
    assert result.retries == 0


async def test_run_ai_structured_wraps_typed_output() -> None:
    agent = Agent(TestModel(custom_output_args={"value": "ok"}), output_type=StructuredOutput)

    result = await run_ai_structured(agent, "Return structured output")

    assert result.output == StructuredOutput(value="ok")
    assert result.usage.total_tokens
