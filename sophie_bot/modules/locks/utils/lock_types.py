from __future__ import annotations
from enum import StrEnum


class LockType(StrEnum):
    ALL = "all"
    ALBUM = "album"
    ANON_CHANNEL = "anonchannel"
    AUDIO = "audio"
    BOT = "bot"
    BOT_LINK = "botlink"
    BUTTON = "button"
    CASHTAG = "cashtag"
    CHECKLIST = "checklist"
    CJK = "cjk"
    COMMAND = "command"
    COMMENT = "comment"
    CONTACT = "contact"
    CYRILLIC = "cyrillic"
    DOCUMENT = "document"
    EMAIL = "email"
    EMOJI = "emoji"
    EMOJI_CUSTOM = "emojicustom"
    EMOJI_GAME = "emojigame"
    EMOJI_ONLY = "emojionly"
    EXTERNAL_REPLY = "externalreply"
    FORWARD = "forward"
    FORWARD_BOT = "forwardbot"
    FORWARD_CHANNEL = "forwardchannel"
    FORWARD_STORY = "forwardstory"
    FORWARD_USER = "forwarduser"
    GAME = "game"
    GIF = "gif"
    INLINE = "inline"
    INVITE_LINK = "invitelink"
    LOCATION = "location"
    PHONE = "phone"
    PHOTO = "photo"
    POLL = "poll"
    RTL = "rtl"
    SPOILER = "spoiler"
    STICKER = "sticker"
    STICKER_ANIMATED = "stickeranimated"
    STICKER_PREMIUM = "stickerpremium"
    TEXT = "text"
    URL = "url"
    VIDEO = "video"
    VIDEO_NOTE = "videonote"
    VOICE = "voice"
    ZALGO = "zalgo"
    DICE = "dice"


ALL_LOCK_TYPES: tuple[str, ...] = tuple(lt.value for lt in LockType)


def is_stickerpack_lock(lock_type: str) -> bool:
    return lock_type.startswith("stickerpack:")


def get_stickerpack_name(lock_type: str) -> str | None:
    if not is_stickerpack_lock(lock_type):
        return None
    parts = lock_type.split(":", 1)
    return parts[1] if len(parts) > 1 else None


def is_language_lock(lock_type: str) -> bool:
    return lock_type.startswith("language:")


def get_language_code(lock_type: str) -> str | None:
    if not is_language_lock(lock_type):
        return None
    parts = lock_type.split(":", 1)
    return parts[1].lower() if len(parts) > 1 else None


def is_supported_lock_type(lock_type: str) -> bool:
    return lock_type in ALL_LOCK_TYPES or is_stickerpack_lock(lock_type) or is_language_lock(lock_type)
