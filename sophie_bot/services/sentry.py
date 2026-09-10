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
from typing import Any, cast

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.pydantic_ai import PydanticAIIntegration
from sentry_sdk.integrations.pymongo import PyMongoIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.types import Event

from sophie_bot.config import CONFIG
from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules.error.utils.ignored import SENTRY_IGNORED_EXCEPTIONS
from sophie_bot.utils.logger import log
from sophie_bot.versions import SOPHIE_COMMIT, SOPHIE_VERSION

_BOT_TOKEN_RE = re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+(/)")

# Placeholders written by versions.py (local runs) and build/runtime/Dockerfile (builds without a commit arg).
_UNKNOWN_COMMITS = frozenset({"No commit", "unknown"})


def _scrub_bot_token(url: str) -> str:
    return _BOT_TOKEN_RE.sub(r"\1[REDACTED]\2", url)


def _before_send_transaction(event: Event, hint: dict[str, Any]) -> Event | None:
    """Redact Telegram bot token from outbound request URLs in trace spans."""
    ev = cast(dict[str, Any], event)
    for span in ev.get("spans", []):
        data = span.get("data", {})
        for key in ("url", "http.url", "db.statement"):
            if isinstance(data.get(key), str):
                data[key] = _scrub_bot_token(data[key])
    request = ev.get("request", {})
    if isinstance(request.get("url"), str):
        request["url"] = _scrub_bot_token(request["url"])
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
        enable_logs=CONFIG.sentry_enable_logs,
        traces_sample_rate=CONFIG.sentry_traces_sample_rate,
        profile_session_sample_rate=CONFIG.sentry_profile_session_sample_rate,
        profile_lifecycle="trace",
        stream_gen_ai_spans=True,
        send_default_pii=True,
        before_send_transaction=_before_send_transaction,
    )
