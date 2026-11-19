from __future__ import annotations

from functools import partial
from unittest.mock import AsyncMock

import httpx2
import pytest
from beanie import PydanticObjectId
from openai import AsyncOpenAI, AuthenticationError, DefaultAsyncHttpxClient
from redis.asyncio import Redis

from sophie_bot.modules.ai.utils import ai_retry_status
from sophie_bot.modules.ai.utils.modern_context import semantic_search_notes
from sophie_bot.utils import notes_search


@pytest.mark.parametrize(
    "status_code,recovers,expected_attempts",
    [(None, True, 5), (None, False, 5), (503, True, 5), (503, False, 5), (401, False, 1)],
)
async def test_note_search_retries_embedding_requests_before_falling_back(
    status_code: int | None,
    recovers: bool,
    expected_attempts: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx2.Request] = []
    monkeypatch.setattr(notes_search, "get_openrouter_api_key", AsyncMock(return_value="offline"))
    lexical_search = AsyncMock(return_value=[])
    monkeypatch.setattr(notes_search.NoteModel, "search_chat_notes", lexical_search)
    monkeypatch.setattr(notes_search.NoteModel, "get_chat_notes", AsyncMock(return_value=[]))

    def retry_delay(*_args: object, **_kwargs: object) -> float:
        return 0

    monkeypatch.setattr(AsyncOpenAI, "_calculate_retry_timeout", retry_delay)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if not recovers or len(requests) < 5:
            if status_code is None:
                raise httpx2.ReadTimeout("Request timed out.", request=request)
            return httpx2.Response(status_code, json={"error": {"message": "unavailable"}})
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": notes_search.EMBEDDING_MODEL,
                "data": [{"object": "embedding", "index": 0, "embedding": [0.5, 0.1]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    monkeypatch.setattr(
        ai_retry_status,
        "DefaultAsyncHttpxClient",
        partial(DefaultAsyncHttpxClient, transport=httpx2.MockTransport(handle)),
    )
    progress = AsyncMock()
    chat_iid = PydanticObjectId()
    redis = AsyncMock(spec=Redis)
    with ai_retry_status.ai_retry_status(progress):
        if status_code == 401:
            with pytest.raises(AuthenticationError):
                await semantic_search_notes(chat_iid, "hello", redis=redis)
            lexical_search.assert_not_awaited()
        else:
            assert await semantic_search_notes(chat_iid, "hello", redis=redis) == []
            lexical_search.assert_awaited_once_with(chat_iid, "hello")
    assert len(requests) == expected_attempts
    assert all(request.extensions["timeout"]["read"] == 15 for request in requests)
    assert [call.args for call in progress.await_args_list] == [
        (attempt, 5) for attempt in range(2, expected_attempts + 1)
    ]
