from __future__ import annotations

from fractions import Fraction
from io import BytesIO
from unittest.mock import AsyncMock

import av
import pytest
from aiogram import Bot
from aiogram.types import Video

from sophie_bot.modules.ai.utils.transform_video import _encode_audio_frames_as_ogg, extract_audio_from_video


def _audio_frame(pts: int) -> av.AudioFrame:
    frame = av.AudioFrame(format="s16", layout="mono", samples=1024)
    frame.sample_rate = 48000
    frame.time_base = Fraction(1, frame.sample_rate)
    frame.pts = pts
    for plane in frame.planes:
        plane.update(bytes(plane.buffer_size))
    return frame


def test_encode_audio_frames_handles_backwards_source_timestamps() -> None:
    frames = [_audio_frame(pts) for pts in (0, 1024, 2048, 1024, 2048)]

    audio_bytes = _encode_audio_frames_as_ogg(frames)

    assert audio_bytes.startswith(b"OggS")
    with av.open(BytesIO(audio_bytes)) as audio_container:
        assert sum(1 for _frame in audio_container.decode(audio=0)) > 0


@pytest.mark.asyncio
async def test_extract_audio_from_video_when_video_stream_precedes_audio() -> None:
    video_buffer = BytesIO()
    with av.open(video_buffer, mode="w", format="mp4") as container:
        video_stream = container.add_stream("mpeg4", rate=24)
        video_stream.width = 16
        video_stream.height = 16
        audio_stream = container.add_stream("aac", rate=48000)
        audio_stream.layout = "mono"

        video_frame = av.VideoFrame(16, 16, format="yuv420p")
        for packet in video_stream.encode(video_frame):
            container.mux(packet)
        for packet in video_stream.encode():
            container.mux(packet)

        for packet in audio_stream.encode(_audio_frame(0)):
            container.mux(packet)
        for packet in audio_stream.encode():
            container.mux(packet)

    video_bytes = video_buffer.getvalue()
    with av.open(BytesIO(video_bytes)) as input_container:
        assert [stream.type for stream in input_container.streams] == ["video", "audio"]

    bot = AsyncMock(spec=Bot)
    bot.download.return_value = BytesIO(video_bytes)
    video = Video(file_id="video", file_unique_id="unique", width=16, height=16, duration=1)

    audio_bytes = await extract_audio_from_video(video, bot=bot)

    assert audio_bytes is not None
    with av.open(BytesIO(audio_bytes)) as audio_container:
        assert sum(1 for _frame in audio_container.decode(audio=0)) > 0
