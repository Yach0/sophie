from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

import httpx
import httpx2
import sentry_sdk
from openai import OpenAIError
from pydantic_ai.exceptions import (
    FallbackExceptionGroup,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from stfu_tg import Code, Doc, KeyValue, Title
from stfu_tg.doc import Element
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential

from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

# openai and pydantic-ai speak httpx2; mistralai and the Tavily search tool still speak legacy
# httpx. The two libraries' exception trees are unrelated -- httpx2.HTTPError is not a subclass of
# httpx.HTTPError -- so every handler that classifies a transport failure has to name both.
_HTTP_ERRORS: Final = (httpx.HTTPError, httpx2.HTTPError)
_HTTP_STATUS_ERRORS: Final = (httpx.HTTPStatusError, httpx2.HTTPStatusError)
# TimeoutException subclasses RequestError in both libraries, so RequestError alone covers timeouts.
_HTTP_REQUEST_ERRORS: Final = (httpx.RequestError, httpx2.RequestError)

# ModelAPIError covers provider failures that never produced an HTTP status (ModelHTTPError is a
# subclass of it), and FallbackExceptionGroup is what pydantic-ai raises when every model in a
# FallbackModel failed. Both used to escape as unclassified crashes with no AI context in Sentry.
AI_PROVIDER_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
    ModelAPIError,
    FallbackExceptionGroup,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    *_HTTP_ERRORS,
    OpenAIError,
    TimeoutError,
)
AI_REQUEST_RETRY_ATTEMPTS: Final = 5
AI_REQUEST_RETRY_WAIT: Final = wait_exponential(multiplier=1, min=1, max=8)
AI_RETRYABLE_STATUS_CODES: Final = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# Statuses that say the deployment is misconfigured rather than that this model refused this
# request: a rejected key, a disabled account or an empty balance answers the same way whatever
# model is asked for, so neither a retry nor another candidate can turn it into an answer.
AI_CONFIGURATION_ERROR_STATUS_CODES: Final = frozenset({401, 402, 403})
_OPENROUTER_PROVIDER_ERROR_STATUS_CODE: Final = 400
_OPENROUTER_PROVIDER_ERROR_TEXT: Final = "provider returned error"
AIRetryCallback = Callable[[int, int], Awaitable[None]]
_PROVIDER_MESSAGE_LIMIT: Final = 500
type _SentryLevel = Literal["warning", "error"]


class AIRequestFailed(SophieException):
    """Raised when an AI provider request fails after retry handling."""

    def __init__(self, sentry_event_id: str | None, *docs: str | Element) -> None:
        super().__init__(*(docs or (_("The AI request failed. Please try again in a moment."),)))
        self.sentry_event_id = sentry_event_id
        Exception.__init__(self, "AI request failed")


@dataclass(frozen=True, slots=True)
class AIErrorContext:
    """Attribution for an AI provider failure, used to tag and group its Sentry event."""

    operation: str
    model_name: str | None = None
    """Model the failing request was sent to."""
    primary_model_name: str | None = None
    """Set when this request was already a fallback for a model that failed first."""


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
    if isinstance(error, _HTTP_STATUS_ERRORS):
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

    if isinstance(error, _HTTP_STATUS_ERRORS):
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


def is_provider_configuration_error(error: BaseException) -> bool:
    """Whether the provider rejected Sophie itself rather than this particular request.

    Neither retrying nor moving to another model fixes a key the provider will not accept. The
    retry loop rules these out by omission, since these statuses are absent from
    AI_RETRYABLE_STATUS_CODES; this check is what stops the failover loop from walking the rest of
    the chain into the same refusal.
    """
    return _get_status_code(error) in AI_CONFIGURATION_ERROR_STATUS_CODES


def is_retryable_ai_provider_error(error: BaseException) -> bool:
    if isinstance(error, UsageLimitExceeded):
        return False
    if isinstance(error, (TimeoutError, *_HTTP_REQUEST_ERRORS)):
        return True
    if isinstance(error, UnexpectedModelBehavior):
        return True

    status_code = _get_status_code(error)
    if status_code in AI_RETRYABLE_STATUS_CODES:
        return True

    error_message = _get_error_message(error).lower()
    return status_code == _OPENROUTER_PROVIDER_ERROR_STATUS_CODE and _OPENROUTER_PROVIDER_ERROR_TEXT in error_message


def _error_details(error: BaseException, context: AIErrorContext) -> dict[str, Any]:
    return {
        "operation": context.operation,
        "model": context.model_name,
        "primary_model": context.primary_model_name,
        "error_type": type(error).__name__,
        "status_code": _get_status_code(error),
        "provider_message": _get_error_message(error)[:_PROVIDER_MESSAGE_LIMIT],
    }


def capture_ai_error(
    error: Exception,
    context: AIErrorContext,
    level: _SentryLevel = "error",
) -> str | None:
    """Report an AI provider failure to Sentry with the model and operation attached."""
    details = _error_details(error, context)
    with sentry_sdk.new_scope() as scope:
        scope.level = level
        scope.set_context("ai_request", details)
        scope.set_tag("ai.operation", context.operation)
        scope.set_tag("ai.error_type", details["error_type"])
        scope.set_tag("ai.is_fallback", context.primary_model_name is not None)
        if context.model_name:
            scope.set_tag("ai.model", context.model_name)
        if details["status_code"] is not None:
            scope.set_tag("ai.status_code", str(details["status_code"]))
        # Provider failures surface through tenacity and anyio frames, so Sentry's default stack
        # grouping merges every model and status code into a single unactionable issue.
        scope.fingerprint = [
            "ai-provider-error",
            context.operation,
            context.model_name or "unknown",
            details["error_type"],
            str(details["status_code"]),
        ]
        return sentry_sdk.capture_exception(error)


def add_ai_retry_breadcrumb(error: BaseException, context: AIErrorContext, attempt: int) -> None:
    """Record a failed attempt so the eventual Sentry event shows the whole retry history."""
    sentry_sdk.add_breadcrumb(
        category="ai.retry",
        level="warning",
        message=f"{context.operation} on {context.model_name} failed, attempt {attempt}/{AI_REQUEST_RETRY_ATTEMPTS}",
        data=_error_details(error, context),
    )


async def run_ai_request_with_retries[RetryableAIOutputT](
    operation: Callable[[], Awaitable[RetryableAIOutputT]],
    context: AIErrorContext,
    on_retry: AIRetryCallback | None = None,
) -> RetryableAIOutputT:
    async def before_sleep(retry_state: RetryCallState) -> None:
        outcome = retry_state.outcome
        if outcome is not None and (error := outcome.exception()) is not None:
            add_ai_retry_breadcrumb(error, context, retry_state.attempt_number)
        if on_retry is None:
            return
        await on_retry(retry_state.attempt_number, AI_REQUEST_RETRY_ATTEMPTS)

    retrying = AsyncRetrying(
        retry=retry_if_exception(is_retryable_ai_provider_error),
        wait=AI_REQUEST_RETRY_WAIT,
        stop=stop_after_attempt(AI_REQUEST_RETRY_ATTEMPTS),
        reraise=True,
        before_sleep=before_sleep,
    )
    async for attempt in retrying:
        with attempt:
            return await operation()

    raise RuntimeError("AI retry loop finished without returning or raising")


def ai_request_failed_from_error(error: Exception, context: AIErrorContext) -> AIRequestFailed:
    if isinstance(error, AIRequestFailed):
        return error
    return AIRequestFailed(capture_ai_error(error, context))


_DEFAULT_AI_FAILED_TITLE: Final = l_("🤖 AI request failed")


def ai_request_failed_message(
    sentry_event_id: str | None,
    title: str | LazyProxy | Element = _DEFAULT_AI_FAILED_TITLE,
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
