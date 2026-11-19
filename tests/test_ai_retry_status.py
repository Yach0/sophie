from __future__ import annotations

import asyncio
import json
from functools import partial
from unittest.mock import AsyncMock

import httpx2
import pytest
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from sophie_bot.modules.ai.utils import ai_retry_status
from sophie_bot.modules.ai.utils.ai_run import run_ai_stream, run_ai_text


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("openrouter", [False, True])
async def test_chat_sdk_retries_report_each_attempt(
    streaming: bool, openrouter: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    progress = AsyncMock()

    def retry_delay(*_args: object, **_kwargs: object) -> float:
        return 0

    monkeypatch.setattr(AsyncOpenAI, "_calculate_retry_timeout", retry_delay)

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 5:
            raise httpx2.ReadTimeout("timed out", request=request)
        response = {
            "id": "offline",
            "created": 0,
            "provider": "offline",
            "model": "openai/test",
            "object": "chat.completion.chunk" if streaming else "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "delta" if streaming else "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ],
        }
        if streaming:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(response) + "\n\ndata: [DONE]\n\n",
            )
        return httpx2.Response(200, json=response)

    monkeypatch.setattr(
        ai_retry_status,
        "DefaultAsyncHttpxClient",
        partial(DefaultAsyncHttpxClient, transport=httpx2.MockTransport(handle)),
    )
    async with ai_retry_status.retry_status_http_client() as http_client:
        model = (
            OpenRouterModel("openai/test", provider=OpenRouterProvider(api_key="offline", http_client=http_client))
            if openrouter
            else OpenAIChatModel("openai/test", provider=OpenAIProvider(api_key="offline", http_client=http_client))
        )
        agent = Agent(model)
        monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_run._last_resort_candidate", lambda candidates: None)
        if streaming:
            result = await run_ai_stream(agent, "hello", AsyncMock(), on_retry=progress)
        else:
            result = await run_ai_text(agent, "hello", on_retry=progress)
    assert result.output == "done"
    assert attempts == 5
    assert [call.args for call in progress.await_args_list] == [(2, 5), (3, 5), (4, 5), (5, 5)]


async def test_retry_callbacks_are_isolated_between_tasks_and_reset_after_failure() -> None:
    first = AsyncMock()
    second = AsyncMock()

    async def report(callback: AsyncMock) -> None:
        with ai_retry_status.ai_retry_status(callback):
            await asyncio.sleep(0)
            await ai_retry_status.report_provider_retry(
                httpx2.Request("POST", "https://offline", headers={"x-stainless-retry-count": "1"})
            )

    await asyncio.gather(report(first), report(second))
    first.assert_awaited_once_with(2, 5)
    second.assert_awaited_once_with(2, 5)
    with pytest.raises(ValueError), ai_retry_status.ai_retry_status(first):
        raise ValueError("cancel request")
    await ai_retry_status.report_provider_retry(
        httpx2.Request("POST", "https://offline", headers={"x-stainless-retry-count": "1"})
    )
    first.assert_awaited_once()
