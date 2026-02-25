# Copyright (C) 2018 - 2020 MrYacha. All rights reserved. Source code available under the AGPL.
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

"""Centralized constants for Sophie Bot.

This module contains constants that are used across multiple modules.
Module-specific constants should remain in their respective modules.
"""

from typing import Final

# =============================================================================
# Telegram API Limits
# =============================================================================

# Maximum length of a Telegram message
TELEGRAM_MESSAGE_LENGTH_LIMIT: Final[int] = 4096

# Maximum length of callback data in inline keyboards
TELEGRAM_CALLBACK_DATA_MAX_LENGTH: Final[int] = 64

# Telegram's anonymous admin bot ID (used when admins post anonymously)
TELEGRAM_ANONYMOUS_ADMIN_BOT_ID: Final[int] = 1087968824

# =============================================================================
# AI Module Limits
# =============================================================================

# Default daily limit for AI requests per user
AI_DEFAULT_DAILY_LIMIT: Final[int] = 150

# Maximum number of AI filter handlers per chat
AI_FILTER_LIMIT_PER_CHAT: Final[int] = 2

# Maximum video file size for AI transcription (in bytes) - 20MB
AI_MAX_VIDEO_SIZE_BYTES: Final[int] = 20 * 1024 * 1024

# =============================================================================
# Session TTLs
# =============================================================================

# Action Config Wizard session timeout (in seconds)
ACW_SESSION_TTL_SECONDS: Final[int] = 20 * 60  # 20 minutes

# Federation transfer request timeout (in seconds)
FEDERATION_TRANSFER_TTL_SECONDS: Final[int] = 300  # 5 minutes


# =============================================================================
# Module Constants
# =============================================================================

# Federation limits
MAX_FEDERATION_NAME_LENGTH: Final[int] = 60
MAX_FEDERATIONS_PER_USER: Final[int] = 1  # Unless owner
MAX_SUBSCRIPTIONS_PER_FEDERATION: Final[int] = 10
MAX_ADMINS_PER_FEDERATION: Final[int] = 50

# Federation ID format constants
FEDERATION_ID_HYPHEN_COUNT: Final[int] = 4
FEDERATION_ID_PART_LENGTH: Final[int] = 4

# Federation operation timeouts
FEDERATION_BAN_TIMEOUT: Final[int] = 30  # seconds
IMPORT_EXPORT_RATE_LIMIT: Final[int] = 600  # seconds
FEDERATION_BANLIST_COOLDOWN_SECONDS: Final[int] = 60  # seconds
SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS: Final[int] = 10  # seconds

# Federation file size limits
MAX_IMPORT_FILE_SIZE_JSON: Final[int] = 1_000_000  # 1MB
MAX_IMPORT_FILE_SIZE_CSV: Final[int] = 50_000_000  # 50MB

# Federation export limits
MAX_BANLIST_EXPORT_SIZE: Final[int] = 500000  # Maximum bans to export
MAX_FCHECK_INLINE_ITEMS: Final[int] = 50  # Maximum inline items before exporting /fcheck results
FEDERATION_EXPORT_TTL_DAYS: Final[int] = 7  # Clean up old export tasks after 7 days

# Welcomesecurity ban timeout (in hours)
WELCOMESECURITY_BAN_TIMEOUT_HOURS: Final[int] = 48

# Welcomesecurity join timeout (in minutes) - skip captcha for old joins if bot was down
WELCOMESECURITY_JOIN_TIMEOUT_MINUTES: Final[int] = 15

# Maximum number of filter triggers per message
FILTERS_MAX_TRIGGERS: Final[int] = 2

# Antiflood limits
ANTIFOOD_MAX_ACTIONS: Final[int] = 1  # Maximum number of actions allowed per chat

# AI Emoji used in messages
AI_EMOJI: Final[str] = "✨"


# =============================================================================
# Cache TTLs
# =============================================================================

# Default cache TTL (in seconds)
CACHE_DEFAULT_TTL_SECONDS: Final[int] = 1800  # 30 minutes

# Language cache TTL (in seconds)
CACHE_LANGUAGE_TTL_SECONDS: Final[int] = 86400  # 24 hours

# Admin cache TTL (in seconds)
CACHE_ADMIN_TTL_SECONDS: Final[int] = 7200  # 2 hours


# =============================================================================
# Metrics
# =============================================================================

# Default histogram buckets for Prometheus metrics
METRICS_HISTOGRAM_BUCKETS: Final[list[float]] = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60]
