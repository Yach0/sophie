from collections.abc import Generator
from contextlib import contextmanager

from sophie_bot.modules.ai.utils.ai_errors import (
    AI_PROVIDER_EXCEPTIONS,
    AIErrorContext,
    ai_error_scope,
    ai_request_failed_from_error,
)
from sophie_bot.utils.logger import log


@contextmanager
def transcription_errors() -> Generator[None]:
    context = AIErrorContext(operation="transcription", model_name="voxtral-mini-latest")
    with ai_error_scope(context):
        try:
            yield
        except AI_PROVIDER_EXCEPTIONS as error:
            log.warning(
                "Audio transcription failed",
                model=context.model_name,
                error_type=type(error).__name__,
                status_code=getattr(error, "status_code", None),
            )
            raise ai_request_failed_from_error(error, context) from error
