from __future__ import annotations

from collections.abc import Iterable
from io import BufferedReader, BytesIO
from time import perf_counter
from typing import BinaryIO

import av
from aiogram import Bot
from aiogram.types import Video, VideoNote
from av.audio.frame import AudioFrame
from av.audio.resampler import AudioResampler
from redis.asyncio import Redis

from sophie_bot.constants import AI_MAX_VIDEO_SIZE_BYTES
from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client
from sophie_bot.modules.ai.utils.ai_telemetry import ai_span
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


def _encode_audio_frames_as_ogg(frames: Iterable[AudioFrame]) -> bytes:
    output_buffer = BytesIO()

    with av.open(output_buffer, mode="w", format="ogg") as output_container:
        output_audio_stream = output_container.add_stream("libopus", rate=24000)
        output_audio_stream.layout = "mono"
        resampler = AudioResampler(
            format="s16",
            layout="mono",
            rate=24000,
        )

        for frame in frames:
            # Source timestamps can jump backwards after MP4 edits or concatenation. The
            # transcription output is continuous audio, so make the resampler derive its
            # own monotonic timestamps from the sample sequence.
            frame.pts = None
            for resampled_frame in resampler.resample(frame):
                for packet in output_audio_stream.encode(resampled_frame):
                    output_container.mux(packet)

        for resampled_frame in resampler.resample(None):
            for packet in output_audio_stream.encode(resampled_frame):
                output_container.mux(packet)

        for packet in output_audio_stream.encode():
            output_container.mux(packet)

    return output_buffer.getvalue()


async def extract_audio_from_video(video: Video | VideoNote, *, bot: Bot) -> bytes | None:
    """Extract audio from video file using PyAV.

    Downloads the video file from Telegram, extracts audio using PyAV,
    and returns the audio bytes in OGG format suitable for transcription.

    Args:
        bot: Telegram bot used to download the video.
        video: The video object from Telegram (Video or VideoNote)

    Returns:
        Optional[bytes]: The extracted audio in OGG format, or None if no audio stream is present.
    """
    # Check video file size before downloading
    video_file_size = getattr(video, "file_size", None)
    if video_file_size is not None and video_file_size > AI_MAX_VIDEO_SIZE_BYTES:
        log.debug(
            "Video file too large for AI transcription",
            file_size=video_file_size,
            max_size=AI_MAX_VIDEO_SIZE_BYTES,
        )
        return None

    downloaded_video: BinaryIO | None = await bot.download(video.file_id)

    if not downloaded_video:
        raise SophieException(_("Failed to download video file"))

    video_bytes = downloaded_video.read()

    with av.open(BytesIO(video_bytes), mode="r") as input_container:
        audio_stream = next((stream for stream in input_container.streams if stream.type == "audio"), None)
        if audio_stream is None:
            log.debug("No audio stream found in video")
            return None

        audio_bytes = _encode_audio_frames_as_ogg(input_container.decode(audio=audio_stream.index))

    if not audio_bytes:
        log.debug("Extracted audio is empty")
        return None
    return audio_bytes


async def transform_video_to_text(video: Video | VideoNote, *, bot: Bot, redis: Redis) -> str | None:
    """Transcribe video audio to text using Mistral AI.

    Downloads the video, extracts audio, and transcribes it using
    the Mistral transcription API.

    Args:
        bot: Telegram bot used to download the video.
        redis: Redis connection used to resolve the transcription client.
        video: The video object from Telegram

    Returns:
        Optional[str]: The transcribed text, or None if the video has no audio.
    """
    with ai_span(
        "ai.transcription", provider="mistral", model="voxtral-mini-latest", input_count=1, media="video"
    ) as span:
        started = perf_counter() if span is not None else 0.0
        try:
            audio_bytes = await extract_audio_from_video(video, bot=bot)

            if audio_bytes is None:
                if span is not None:
                    span.set_attribute("outcome", "no_audio")
                return None

            audio_bytes_io = BufferedReader(BytesIO(audio_bytes))

            client = await get_mistral_client(redis=redis)
            resp = await client.audio.transcriptions.complete_async(
                model="voxtral-mini-latest",
                file={
                    "file_name": "audio.ogg",
                    "content": audio_bytes_io,
                    "content_type": "audio/ogg",
                },
            )

            transcribed_text = resp.text.removesuffix("\n")
            log.debug("Transcribed text", transcribed_text=transcribed_text)
            if span is not None:
                span.set_attribute("outcome", "success")
            return transcribed_text
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
