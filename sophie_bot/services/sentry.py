# Copyright (C) 2018 - 2020 MrYacha. All rights reserved. Source code available under the AGPL.
# Copyright (C) 2019 Aiogram
#
# This file is part of SophieBot.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import re
from typing import Any

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.pydantic_ai import PydanticAIIntegration
from sentry_sdk.integrations.pymongo import PyMongoIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, SpanJSON

from sophie_bot.config import CONFIG
from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules.ai.utils.ai_errors import (
    AIRequestFailed,
    current_ai_error_context,
    is_ai_provider_failure,
)
from sophie_bot.modules.error.utils.ignored import SENTRY_IGNORED_EXCEPTIONS
from sophie_bot.utils.logger import log
from sophie_bot.versions import SOPHIE_COMMIT, SOPHIE_VERSION

_BOT_TOKEN_RE = re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+(/)")

# Placeholders written by versions.py (local runs) and build/runtime/Dockerfile (builds without a commit arg).
_UNKNOWN_COMMITS = frozenset({"No commit", "unknown"})


def _scrub_bot_token(url: str) -> str:
    return _BOT_TOKEN_RE.sub(r"\1[REDACTED]\2", url)


def _before_send_span(span: SpanJSON, _hint: dict[str, Any]) -> SpanJSON:
    """Redact Telegram bot tokens before Sentry serializes streamed spans."""
    span["name"] = _scrub_bot_token(span["name"])
    attributes = span.get("attributes") or {}
    for key, value in attributes.items():
        if isinstance(value, str):
            attributes[key] = _scrub_bot_token(value)
    return span


def _before_breadcrumb(crumb: Breadcrumb, _hint: BreadcrumbHint) -> Breadcrumb:
    """Redact token-bearing URLs in HTTP breadcrumbs copied to error events."""
    data = crumb.get("data")
    if isinstance(data, dict) and isinstance(url := data.get("url"), str):
        crumb["data"] = {**data, "url": _scrub_bot_token(url)}
    return crumb


def _before_send(event: Event, hint: dict[str, Any]) -> Event | None:
    """Drop provider retry events only while Sophie's AI boundary owns reporting.

    PydanticAIIntegration hooks Agent.run and emits an unhandled error event on every failing
    attempt before retries/failovers occur. Sophie's ai_run candidate loop handles retries with
    exponential backoff and reports terminal failures with full AI context and fingerprinting.
    """
    request = event.get("request")
    if request is not None and isinstance(url := request.get("url"), str):
        request["url"] = _scrub_bot_token(url)
    exc_values = event.get("exception", {}).get("values", [])
    exc_info = hint.get("exc_info")
    error = exc_info[1] if exc_info else None
    context = current_ai_error_context()
    for exc in exc_values:
        mechanism = exc.get("mechanism")
        if mechanism and mechanism.get("type") == "pydantic_ai" and not mechanism.get("handled"):
            if (
                context is not None
                and isinstance(error, BaseException)
                and (isinstance(error, AIRequestFailed) or is_ai_provider_failure(error))
            ):
                return None
            if isinstance(error, Exception):
                if isinstance(getattr(error, "sentry_event_id", None), str):
                    return None
                # Preserve unexpected integration events and reuse their reference at the global
                # handler, rather than emitting the same exception for a second time.
                error.__dict__["sentry_event_id"] = event.get("event_id")
                if context is not None:
                    event.setdefault("tags", {}).update(
                        {"ai.origin": context.origin, "ai.operation": context.operation}
                    )
                    event.setdefault("contexts", {})["ai_request"] = {
                        "origin": context.origin,
                        "operation": context.operation,
                        "model": context.model_name,
                        "error_type": type(error).__name__,
                    }
    return event


def build_release() -> str:
    """Build a per-build Sentry release identifier.

    The version alone is not unique per build: it only changes on a version bump, so every commit
    in between reports as the same release and Sentry cannot tell builds apart.
    """
    if SOPHIE_COMMIT in _UNKNOWN_COMMITS:
        return SOPHIE_VERSION
    return f"{SOPHIE_VERSION}+{SOPHIE_COMMIT}"


def init_sentry() -> None:
    log.info("Starting sentry.io integraion...")

    integrations: list[Integration] = [
        RedisIntegration(),
        AioHttpIntegration(),
        PyMongoIntegration(),
        PydanticAIIntegration(handled_tool_call_exceptions=False),
    ]

    if CONFIG.sentry_enable_logs:
        integrations.append(
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR,
            )
        )

    sentry_sdk.init(
        str(CONFIG.sentry_url),
        integrations=integrations,
        environment=f"{CONFIG.environment}_{SOPHIE_MODE}",
        release=build_release(),
        ignore_errors=SENTRY_IGNORED_EXCEPTIONS,
        default_integrations=False,
        # Preserve the sanitized message-only AI failure events.
        attach_stacktrace=False,
        traces_sample_rate=CONFIG.sentry_traces_sample_rate,
        profile_session_sample_rate=CONFIG.sentry_profile_session_sample_rate,
        profile_lifecycle="trace",
        data_collection={
            "gen_ai": {"inputs": True, "outputs": True},
            "stack_frame_variables": {"mode": "denylist", "terms": ["api_key"]},
        },
        before_breadcrumb=_before_breadcrumb,
        before_send_span=_before_send_span,
        before_send=_before_send,
    )
