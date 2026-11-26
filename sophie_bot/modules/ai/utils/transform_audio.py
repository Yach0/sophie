from mimetypes import guess_extension, guess_type

from aiogram import Bot
from aiogram.types import Audio, Voice
from redis.asyncio import Redis

from sophie_bot.modules.ai.utils.media_download import MediaLimitError, download_media
from sophie_bot.modules.ai.utils.transcription import transcribe_audio

MAX_AUDIO_DURATION = 600


class EmptyAudioError(ValueError):
    """Telegram returned no audio data to transcribe."""


async def transform_voice_to_text(voice: Voice | Audio, *, bot: Bot, redis: Redis) -> str:
    if voice.duration > MAX_AUDIO_DURATION:
        raise MediaLimitError("Audio duration exceeds transcription limit")
    raw_bytes = await download_media(voice.file_id, voice.file_size, bot=bot)
    if not raw_bytes:
        raise EmptyAudioError("Downloaded audio file is empty")

    file_name = voice.file_name if isinstance(voice, Audio) else "audio.ogg"
    content_type = voice.mime_type or (guess_type(file_name or "")[0] if isinstance(voice, Audio) else "audio/ogg")
    content_type = content_type or "audio/mpeg"
    file_name = file_name or "audio" + (guess_extension(content_type) or ".bin")
    return await transcribe_audio(raw_bytes, file_name, content_type, redis=redis)
