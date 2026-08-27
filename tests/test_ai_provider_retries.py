from __future__ import annotations

from typing import Any

import httpx
import httpx2
import pytest
from pydantic_ai import ModelHTTPError

from sophie_bot.modules.ai.utils.ai_errors import AI_PROVIDER_EXCEPTIONS, is_retryable_ai_provider_error


def test_openrouter_provider_error_is_retryable() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="test-model",
        body={"error": {"message": "Provider returned error"}},
    )

    assert is_retryable_ai_provider_error(error)


def test_regular_bad_request_is_not_retryable() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="test-model",
        body={"error": {"message": "Invalid request"}},
    )

    assert not is_retryable_ai_provider_error(error)


def test_transient_status_code_is_retryable() -> None:
    error = ModelHTTPError(
        status_code=503,
        model_name="test-model",
        body={"error": {"message": "Service unavailable"}},
    )

    assert is_retryable_ai_provider_error(error)


# openai and pydantic-ai moved to httpx2, while mistralai and the Tavily search tool still raise
# legacy httpx exceptions. The two exception trees are unrelated, so classification has to keep
# working for both; catching only one silently stops retrying half the provider failures.
@pytest.mark.parametrize("http", [httpx, httpx2], ids=["httpx", "httpx2"])
def test_transport_error_is_retryable_on_both_http_stacks(http: Any) -> None:
    assert is_retryable_ai_provider_error(http.ConnectError("connection refused"))
    assert is_retryable_ai_provider_error(http.ReadTimeout("timed out"))


@pytest.mark.parametrize("http", [httpx, httpx2], ids=["httpx", "httpx2"])
def test_status_error_retryability_is_read_on_both_http_stacks(http: Any) -> None:
    request = http.Request("GET", "https://provider.example/v1/chat")

    retryable = http.HTTPStatusError(
        "service unavailable",
        request=request,
        response=http.Response(503, request=request),
    )
    not_retryable = http.HTTPStatusError(
        "unauthorized",
        request=request,
        response=http.Response(401, request=request, json={"error": {"message": "Invalid key"}}),
    )

    assert is_retryable_ai_provider_error(retryable)
    assert not is_retryable_ai_provider_error(not_retryable)


@pytest.mark.parametrize("http", [httpx, httpx2], ids=["httpx", "httpx2"])
def test_ai_provider_exceptions_cover_both_http_stacks(http: Any) -> None:
    try:
        raise http.ConnectError("connection refused")
    except AI_PROVIDER_EXCEPTIONS:
        pass


def test_mistral_sdk_error_status_code_retryability() -> None:
    from mistralai.client.errors import SDKError

    request = httpx.Request("POST", "https://api.mistral.ai/v1/chat/moderations")
    response_503 = httpx.Response(503, request=request, text="upstream connect error")
    error_503 = SDKError("API error occurred", response_503)

    response_400 = httpx.Response(400, request=request, text="bad request")
    error_400 = SDKError("API error occurred", response_400)

    assert is_retryable_ai_provider_error(error_503)
    assert not is_retryable_ai_provider_error(error_400)

    try:
        raise error_503
    except AI_PROVIDER_EXCEPTIONS:
        pass
