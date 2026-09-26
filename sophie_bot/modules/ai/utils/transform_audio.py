from io import BufferedReader, BytesIO
from time import perf_counter
from typing import BinaryIO

from aiogram import Bot
from aiogram.types import Voice
from redis.asyncio import Redis

from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client
from sophie_bot.modules.ai.utils.ai_telemetry import ai_span


async def transform_voice_to_text(voice: Voice, *, bot: Bot, redis: Redis) -> str:
    with ai_span(
        "ai.transcription", provider="mistral", model="voxtral-mini-latest", input_count=1, media="voice"
    ) as span:
        started = perf_counter() if span is not None else 0.0
        try:
            downloaded_audio: BinaryIO | None = await bot.download(voice.file_id)

            if downloaded_audio is None:
                raise ValueError("Failed to download voice file")

            raw_bytes = downloaded_audio.read()
            if not raw_bytes:
                raise ValueError("Downloaded voice file is empty")

            audio_bytes = BufferedReader(BytesIO(raw_bytes))

            client = await get_mistral_client(redis=redis)
            resp = await client.audio.transcriptions.complete_async(
                model="voxtral-mini-latest",
                file={
                    "file_name": "audio.ogg",
                    "content": audio_bytes,
                    "content_type": "audio/ogg",
                },
            )

            respond: str = resp.text
            respond = respond.removesuffix("\n")
            if span is not None:
                span.set_attribute("outcome", "success")
            return respond
        except Exception as error:
            if span is not None:
                span.set_attribute("outcome", "error")
                span.set_attribute("error_type", type(error).__name__)
                status_code = getattr(error, "status_code", None)
                if isinstance(status_code, int):
                    span.set_attribute("status_code", status_code)
            raise
        finally:
            if span is not None:
                span.set_attribute("duration_ms", (perf_counter() - started) * 1000)
