import sentry_sdk


def capture_sentry(exception: Exception) -> str | None:
    if not sentry_sdk.is_initialized():
        return None
    return sentry_sdk.capture_exception(exception)
