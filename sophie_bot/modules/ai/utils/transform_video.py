from __future__ import annotations

import types as typing_types
from collections.abc import Iterable
from io import BufferedReader, BytesIO
from typing import TYPE_CHECKING, BinaryIO

from aiogram.types import Video, VideoNote

from sophie_bot.constants import AI_MAX_VIDEO_SIZE_BYTES
from sophie_bot.modules.ai.utils.ai_clients import get_mistral_client
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

if TYPE_CHECKING:
    from av.audio.frame import AudioFrame
    from av.audio.resampler import AudioResampler

# Try to import av - if not available, video transcription will be disabled
try:
    import av as _av_module

    AV_AVAILABLE = True
except ImportError:
    AV_AVAILABLE = False
    _av_module: typing_types.ModuleType = None  # ty: ignore[invalid-assignment]


def _encode_audio_frames_as_ogg(frames: Iterable[AudioFrame]) -> bytes:
    output_buffer = BytesIO()

    with _av_module.open(output_buffer, mode="w", format="ogg") as output_container:
        output_audio_stream = output_container.add_stream("libopus", rate=24000)
        output_audio_stream.layout = "mono"
        resampler: AudioResampler = _av_module.audio.resampler.AudioResampler(  # type: ignore[possibly-missing-attribute]
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


async def extract_audio_from_video(video: Video | VideoNote) -> bytes | None:
    """Extract audio from video file using PyAV.

    Downloads the video file from Telegram, extracts audio using PyAV,
    and returns the audio bytes in OGG format suitable for transcription.

    Args:
        video: The video object from Telegram (Video or VideoNote)

    Returns:
        Optional[bytes]: The extracted audio in OGG format, or None if extraction fails

    Raises:
        SophieException: If video download fails
    """
    if not AV_AVAILABLE or _av_module is None:
        log.debug("PyAV not available, skipping audio extraction")
        return None

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

    try:
        with _av_module.open(BytesIO(video_bytes), mode="r") as input_container:
            audio_stream = next((stream for stream in input_container.streams if stream.type == "audio"), None)
            if audio_stream is None:
                log.debug("No audio stream found in video")
                return None

            audio_bytes = _encode_audio_frames_as_ogg(input_container.decode(audio_stream))

        if len(audio_bytes) == 0:
            log.debug("Extracted audio is empty")
            return None

        return audio_bytes

    except Exception as error:  # noqa: BLE001  # media decode boundary: any PyAV/codec failure degrades to no-audio
        log.error("Audio extraction failed", error=str(error))
        return None


async def transform_video_to_text(video: Video | VideoNote) -> str | None:
    """Transcribe video audio to text using Mistral AI.

    Downloads the video, extracts audio, and transcribes it using
    the Mistral transcription API.

    Args:
        video: The video object from Telegram

    Returns:
        Optional[str]: The transcribed text from the video, or None if transcription fails
    """
    audio_bytes = await extract_audio_from_video(video)

    if audio_bytes is None:
        return None

    audio_bytes_io = BufferedReader(BytesIO(audio_bytes))

    client = await get_mistral_client()
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

    return transcribed_text
