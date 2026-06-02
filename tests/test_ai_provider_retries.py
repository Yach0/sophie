from __future__ import annotations

from pydantic_ai import ModelHTTPError

from sophie_bot.modules.ai.utils.ai_errors import is_retryable_ai_provider_error


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
