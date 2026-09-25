import sentry_sdk

from sophie_bot.services.logfire import capture_logfire_error


def capture_sentry(exception: Exception) -> str | None:
    if not sentry_sdk.is_initialized():
        capture_logfire_error(exception)
        return None
    return sentry_sdk.capture_exception(exception)
