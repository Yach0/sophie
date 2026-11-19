from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

import httpx2
from openai import DefaultAsyncHttpxClient

from sophie_bot.modules.ai.utils.ai_errors import AI_REQUEST_RETRY_ATTEMPTS, AIRetryCallback

# Cached provider clients serve many chats; progress callbacks belong to the current task.
_retry_callback: ContextVar[AIRetryCallback | None] = ContextVar("ai_retry_callback", default=None)


@contextmanager
def ai_retry_status(callback: AIRetryCallback | None) -> Generator[None]:
    if callback is None:
        yield
        return
    token = _retry_callback.set(callback)
    try:
        yield
    finally:
        _retry_callback.reset(token)


async def report_provider_retry(request: httpx2.Request) -> None:
    callback = _retry_callback.get()
    retries_taken = int(request.headers.get("x-stainless-retry-count", "0"))
    if callback is not None and retries_taken > 0:
        await callback(retries_taken + 1, AI_REQUEST_RETRY_ATTEMPTS)


def retry_status_http_client() -> httpx2.AsyncClient:
    return DefaultAsyncHttpxClient(event_hooks={"request": [report_provider_retry]})
