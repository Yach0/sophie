from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, TypeVar, cast

from httpx import HTTPError, HTTPStatusError, RequestError, TimeoutException
from openai import OpenAIError
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from stfu_tg import Code, Doc, KeyValue, Title
from stfu_tg.doc import Element
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential

from sophie_bot.modules.error.utils.capture import capture_sentry
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

AI_PROVIDER_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    HTTPError,
    OpenAIError,
    TimeoutError,
)
AI_REQUEST_RETRY_ATTEMPTS: Final = 5
AI_RETRYABLE_STATUS_CODES: Final = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_OPENROUTER_PROVIDER_ERROR_STATUS_CODE: Final = 400
_OPENROUTER_PROVIDER_ERROR_TEXT: Final = "provider returned error"
RetryableAIOutputT = TypeVar("RetryableAIOutputT")
AIRetryCallback = Callable[[int, int], Awaitable[None]]


class AIRequestFailed(SophieException):
    """Raised when an AI provider request fails after retry handling."""

    def __init__(self, sentry_event_id: str | None) -> None:
        self.sentry_event_id = sentry_event_id
        super().__init__(_("The AI request failed. Please try again in a moment."))
        Exception.__init__(self, "AI request failed")


def _get_response_error_message(response_data: object) -> str | None:
    if not isinstance(response_data, Mapping):
        return None

    response_mapping = cast(Mapping[str, object], response_data)
    error_data = response_mapping.get("error")
    if isinstance(error_data, Mapping):
        error_mapping = cast(Mapping[str, object], error_data)
        error_message = error_mapping.get("message")
        if isinstance(error_message, str):
            return error_message

    message = response_mapping.get("message")
    if isinstance(message, str):
        return message
    return None


def _get_status_code(error: BaseException) -> int | None:
    if isinstance(error, ModelHTTPError):
        return error.status_code
    if isinstance(error, HTTPStatusError):
        return error.response.status_code

    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def _get_error_message(error: BaseException) -> str:
    if isinstance(error, ModelHTTPError):
        message = _get_response_error_message(error.body)
        if message:
            return message

    if isinstance(error, HTTPStatusError):
        try:
            message = _get_response_error_message(error.response.json())
        except ValueError:
            message = error.response.text
        if message:
            return message

    body = getattr(error, "body", None)
    message = _get_response_error_message(body)
    if message:
        return message

    return str(error)


def is_retryable_ai_provider_error(error: BaseException) -> bool:
    if isinstance(error, UsageLimitExceeded):
        return False
    if isinstance(error, TimeoutError | TimeoutException | RequestError):
        return True
    if isinstance(error, UnexpectedModelBehavior):
        return True

    status_code = _get_status_code(error)
    if status_code in AI_RETRYABLE_STATUS_CODES:
        return True

    error_message = _get_error_message(error).lower()
    return status_code == _OPENROUTER_PROVIDER_ERROR_STATUS_CODE and _OPENROUTER_PROVIDER_ERROR_TEXT in error_message


async def run_ai_request_with_retries(
    operation: Callable[[], Awaitable[RetryableAIOutputT]],
    on_retry: AIRetryCallback | None = None,
) -> RetryableAIOutputT:
    async def before_sleep(retry_state: RetryCallState) -> None:
        if on_retry is None:
            return
        await on_retry(retry_state.attempt_number, AI_REQUEST_RETRY_ATTEMPTS)

    retrying = AsyncRetrying(
        retry=retry_if_exception(is_retryable_ai_provider_error),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(AI_REQUEST_RETRY_ATTEMPTS),
        reraise=True,
        before_sleep=before_sleep,
    )
    async for attempt in retrying:
        with attempt:
            return await operation()

    raise RuntimeError("AI retry loop finished without returning or raising")


def ai_request_failed_from_error(error: Exception) -> AIRequestFailed:
    if isinstance(error, AIRequestFailed):
        return error
    event_id = capture_sentry(error)
    return AIRequestFailed(event_id)


def ai_request_failed_message(
    sentry_event_id: str | None,
    title: str | LazyProxy | Element = l_("🤖 AI request failed"),
) -> dict[str, Any]:
    return {
        "text": str(
            Doc(
                Title(title),
                _("The AI provider did not complete the request. Please try again in a moment."),
                *(
                    (
                        " ",
                        KeyValue(_("Reference ID"), Code(sentry_event_id)),
                    )
                    if sentry_event_id
                    else ()
                ),
            )
        )
    }
