"""Bound Telegram downloads even when Telegram omits or misreports file size."""

from __future__ import annotations

import asyncio
from collections.abc import Buffer
from io import BytesIO

from aiogram import Bot

from sophie_bot.constants import AI_MAX_VIDEO_SIZE_BYTES

MEDIA_DOWNLOAD_TIMEOUT = 30


class MediaLimitError(ValueError):
    pass


class BoundedMediaBuffer(BytesIO):
    def write(self, buffer: Buffer, /) -> int:
        if self.tell() + memoryview(buffer).nbytes > AI_MAX_VIDEO_SIZE_BYTES:
            raise MediaLimitError("Telegram media exceeds download limit")
        return super().write(buffer)


async def download_media(file_id: str, file_size: int | None, *, bot: Bot) -> bytes:
    if file_size is not None and file_size > AI_MAX_VIDEO_SIZE_BYTES:
        raise MediaLimitError("Telegram media exceeds download limit")
    with BoundedMediaBuffer() as destination:
        async with asyncio.timeout(MEDIA_DOWNLOAD_TIMEOUT):
            await bot.download(file_id, destination=destination)
        return destination.getvalue()
