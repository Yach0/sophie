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

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.pymongo import PyMongoIntegration
from sentry_sdk.integrations.redis import RedisIntegration

from sophie_bot.config import CONFIG
from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules.error.utils.ignored import IGNORED_EXCEPTIONS
from sophie_bot.utils.logger import log
from sophie_bot.versions import SOPHIE_VERSION


def init_sentry() -> None:
    log.info("Starting sentry.io integraion...")

    integrations: list[Integration] = [RedisIntegration(), AioHttpIntegration(), PyMongoIntegration()]

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
        release=SOPHIE_VERSION,
        ignore_errors=IGNORED_EXCEPTIONS,
        default_integrations=False,
        enable_logs=CONFIG.sentry_enable_logs,
        traces_sample_rate=CONFIG.sentry_traces_sample_rate,
        profile_session_sample_rate=CONFIG.sentry_profile_session_sample_rate,
        profile_lifecycle="trace",
        stream_gen_ai_spans=True,
        send_default_pii=True,
    )
