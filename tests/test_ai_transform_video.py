from __future__ import annotations

from fractions import Fraction
from io import BytesIO

import av

from sophie_bot.modules.ai.utils.transform_video import _encode_audio_frames_as_ogg


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
