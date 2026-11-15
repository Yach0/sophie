"""Bounded PyAV sampling worker; the parent enforces a wall-clock deadline."""

from __future__ import annotations

import json
import math
import resource
import sys
from collections.abc import Iterator
from pathlib import Path

import av

MAX_STILL_PIXELS = 4096 * 4096
MAX_STILL_PACKETS = 1200
MAX_FRAMES = 600
MAX_DECODED_PIXELS = 512 * 1024 * 1024


def frames(source: Path) -> Iterator[av.VideoFrame]:
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        if not 0 < stream.width * stream.height <= MAX_STILL_PIXELS:
            raise ValueError("Animation dimensions exceed limit")
        stream.thread_count = 1
        count = 0
        pixels = 0
        for index, packet in enumerate(container.demux(stream)):
            if index >= MAX_STILL_PACKETS:
                raise ValueError("Animation has no usable frame within packet limit")
            for frame in packet.decode():
                count += 1
                pixels += frame.width * frame.height
                if count > MAX_FRAMES or pixels > MAX_DECODED_PIXELS:
                    raise ValueError("Animation exceeds decode budget")
                if not 0 < frame.width * frame.height <= MAX_STILL_PIXELS:
                    raise ValueError("Animation frame exceeds pixel limit")
                yield frame


def decode_samples(source: Path, destination: Path, duration: float | None) -> None:
    # Scan to EOF before emitting anything: truncated/budget-limited input must never
    # pass off an intermediate frame as the final frame. Retain no decoded frames.
    count = 0
    first_time: float | None = None
    end_time: float | None = None
    for frame in frames(source):
        count += 1
        if frame.pts is not None and frame.time_base is not None:
            timestamp = float(frame.pts * frame.time_base)
            if first_time is None:
                first_time = timestamp
            if frame.duration > 0:
                end_time = timestamp + float(frame.duration * frame.time_base)
    if not count:
        raise ValueError("Animation has no usable frame")
    if duration is None:
        with av.open(str(source)) as container:
            stream = container.streams.video[0]
            if stream.duration is not None and stream.time_base is not None:
                duration = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                duration = container.duration / av.time_base
            elif first_time is not None and end_time is not None:
                duration = end_time - first_time
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Animation duration is unknown")
    indices = sorted({min(1, count - 1), count // 2, count - 1}) if duration >= 5 else [0]
    for index, frame in enumerate(frames(source)):
        if index in indices:
            image = frame.to_image()
            image.thumbnail((1024, 1024))
            image.convert("RGB").save(destination / f"{index}.jpg", format="JPEG")
    (destination / "samples.json").write_text(json.dumps({"indices": indices, "count": count, "duration": duration}))


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    decode_samples(Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]) if sys.argv[3] != "unknown" else None)
