from io import BufferedReader, BytesIO
from typing import BinaryIO

from aiogram import Bot
from aiogram.types import Voice
from redis.asyncio import Redis

from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client


async def transform_voice_to_text(voice: Voice, *, bot: Bot, redis: Redis) -> str:
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

    return respond
