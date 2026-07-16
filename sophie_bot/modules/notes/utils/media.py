from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping, Type

from aiogram.enums import ContentType
from aiogram.methods import (
    SendAnimation,
    SendAudio,
    SendDocument,
    SendPhoto,
    SendSticker,
    SendVideo,
    SendVideoNote,
    SendVoice,
    TelegramMethod,
)
from aiogram.types import Message

# Telegram caps a media caption at 1024 characters, far below the 4096 of a plain message.
MEDIA_CAPTION_LENGTH_LIMIT: Final[int] = 1024


@dataclass(frozen=True)
class MediaSpec:
    """How a stored note file is sent back to Telegram.

    ``file_field`` is the send method's field carrying the file id. ``supports_caption``
    is false for sendSticker and sendVideoNote: they take a reply_markup like every other
    send method, but no caption — the two capabilities are not interchangeable.
    """

    method: Type[TelegramMethod[Message]]
    file_field: str
    supports_caption: bool


MEDIA_SPECS: Mapping[ContentType, MediaSpec] = MappingProxyType(
    {
        ContentType.AUDIO: MediaSpec(SendAudio, "audio", supports_caption=True),
        ContentType.ANIMATION: MediaSpec(SendAnimation, "animation", supports_caption=True),
        ContentType.DOCUMENT: MediaSpec(SendDocument, "document", supports_caption=True),
        ContentType.PHOTO: MediaSpec(SendPhoto, "photo", supports_caption=True),
        ContentType.VIDEO: MediaSpec(SendVideo, "video", supports_caption=True),
        ContentType.VOICE: MediaSpec(SendVoice, "voice", supports_caption=True),
        ContentType.STICKER: MediaSpec(SendSticker, "sticker", supports_caption=False),
        ContentType.VIDEO_NOTE: MediaSpec(SendVideoNote, "video_note", supports_caption=False),
    }
)

PARSABLE_CONTENT_TYPES: tuple[ContentType, ...] = tuple(MEDIA_SPECS)
