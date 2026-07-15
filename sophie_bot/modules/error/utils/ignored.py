from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from pymongo.errors import DuplicateKeyError

IGNORED_EXCEPTIONS = (TelegramNetworkError, TelegramRetryAfter, TelegramUnauthorizedError)

# Reported to Sentry, but not worth an event: transient upstream 5xx. The polling loop already
# catches these, backs off exponentially and retries, so no updates are lost -- the events only
# exist because LoggingIntegration promotes aiogram's "Failed to fetch updates" log to an event.
SENTRY_IGNORED_EXCEPTIONS = (*IGNORED_EXCEPTIONS, TelegramServerError)

# Handled silently: no user reply, no Sentry, no FSM cleanup. TelegramServerError is deliberately
# NOT here -- a 5xx on an outgoing call inside a handler means the command failed, and the user
# still needs to be told rather than have it disappear.
QUIET_EXCEPTIONS = (*IGNORED_EXCEPTIONS, DuplicateKeyError)
