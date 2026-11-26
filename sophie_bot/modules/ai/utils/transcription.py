from collections.abc import Generator
from contextlib import contextmanager
from io import BufferedReader, BytesIO

from redis.asyncio import Redis

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_catalog import resolve_native_model
from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client, get_openai_client
from sophie_bot.modules.ai.utils.ai_errors import (
    AI_PROVIDER_EXCEPTIONS,
    AIErrorContext,
    ai_error_scope,
    ai_request_failed_from_error,
)
from sophie_bot.utils.logger import log


@contextmanager
def transcription_errors(model_name: str = "voxtral-mini-latest") -> Generator[None]:
    context = AIErrorContext(operation="transcription", model_name=model_name)
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


async def transcribe_audio(content: bytes, file_name: str, content_type: str, *, redis: Redis) -> str:
    """Native file transcription shared by voice, audio, video and video notes."""
    model = await resolve_native_model(AIMode.support, AIModelPurpose.transcription, redis=redis)
    with BufferedReader(BytesIO(content)) as audio_file, transcription_errors(model.api_name):
        if model.provider.name == "mistral":
            mistral = await get_mistral_client(redis=redis)
            response = await mistral.audio.transcriptions.complete_async(
                model=model.api_name,
                file={"file_name": file_name, "content": audio_file, "content_type": content_type},
            )
            text = response.text
        else:
            openai = await get_openai_client(redis=redis)
            transcription = await openai.audio.transcriptions.create(
                model=model.api_name,
                file=(file_name, audio_file, content_type),
                response_format="json",
            )
            text = transcription.text
    return text.removesuffix("\n")
