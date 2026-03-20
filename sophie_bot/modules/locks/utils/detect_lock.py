from __future__ import annotations

import re
import unicodedata
from typing import Callable

from aiogram.enums import MessageEntityType
from aiogram.types import Message

from sophie_bot.modules.locks.utils.lock_types import (
    LockType,
    get_language_code,
    get_stickerpack_name,
    is_language_lock,
    is_stickerpack_lock,
)
from sophie_bot.modules.ai.utils.detect_lang import lang_code_to_language, is_text_language
from sophie_bot.utils.logger import log

CJK_REGEX = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002b73f\U0002b740-\U0002b81f]")
CYRILLIC_REGEX = re.compile(r"[\u0400-\u04ff\u0500-\u052f]")
RTL_REGEX = re.compile(r"[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
ZALGO_REGEX = re.compile(r"[\u0300-\u036f\u0483-\u0489\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]")
EMOJI_REGEX = re.compile(
    "["
    "\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff"
    "\U00002702-\U000027b0"
    "\U0001f900-\U0001f9ff"
    "\U0001fa70-\U0001faff"
    "\U00002600-\U000026ff"
    "\U00002700-\U000027bf"
    "]"
)
INVITE_LINK_REGEX = re.compile(r"(t\.me/\+|t\.me/joinchat|telegram\.me/\+|telegram\.me/joinchat)", re.IGNORECASE)
BOT_LINK_REGEX = re.compile(r"(t\.me/|telegram\.me/)[a-zA-Z0-9_]+bot\b", re.IGNORECASE)
EMOJI_ONLY_REGEX = re.compile(
    r"^[\s\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001f900-\U0001f9ff\U0001fa70-\U0001faff\U00002600-\U000026ff\U00002700-\U000027bf]*$"
)


def _get_all_entities(message: Message) -> list:
    entities = list(message.entities or [])
    entities.extend(message.caption_entities or [])
    return entities


def _has_entity_type(message: Message, entity_type: str) -> bool:
    entities = _get_all_entities(message)
    return any(e.type == entity_type for e in entities)


def _get_text_content(message: Message) -> str:
    return message.text or message.caption or ""


def _check_cjk(message: Message) -> bool:
    text = _get_text_content(message)
    return bool(CJK_REGEX.search(text))


def _check_cyrillic(message: Message) -> bool:
    text = _get_text_content(message)
    return bool(CYRILLIC_REGEX.search(text))


def _check_rtl(message: Message) -> bool:
    text = _get_text_content(message)
    return bool(RTL_REGEX.search(text))


def _check_zalgo(message: Message) -> bool:
    text = _get_text_content(message)
    if not text:
        return False
    combining_count = len(ZALGO_REGEX.findall(text))
    return combining_count > len(text) * 0.3


def _check_emoji(message: Message) -> bool:
    text = _get_text_content(message)
    return bool(EMOJI_REGEX.search(text))


def _check_emoji_only(message: Message) -> bool:
    text = _get_text_content(message).strip()
    if not text:
        return False
    normalized = unicodedata.normalize("NFC", text)
    return bool(EMOJI_ONLY_REGEX.match(normalized))


def _check_emoji_custom(message: Message) -> bool:
    return _has_entity_type(message, MessageEntityType.CUSTOM_EMOJI)


def _check_stickerpack(message: Message, pack_name: str) -> bool:
    if not message.sticker:
        return False
    return message.sticker.set_name == pack_name


def _check_forward(message: Message) -> bool:
    return bool(message.forward_from or message.forward_from_chat)


def _check_forward_bot(message: Message) -> bool:
    if not message.forward_from:
        return False
    return message.forward_from.is_bot


def _check_forward_channel(message: Message) -> bool:
    if not message.forward_from_chat:
        return False
    return message.forward_from_chat.type == "channel"


def _check_forward_story(message: Message) -> bool:
    return bool(getattr(message, "forward_from_story", None))


def _check_forward_user(message: Message) -> bool:
    if not message.forward_from:
        return False
    return not message.forward_from.is_bot


def _check_url(message: Message) -> bool:
    return _has_entity_type(message, MessageEntityType.URL) or _has_entity_type(message, MessageEntityType.TEXT_LINK)


def _check_invite_link(message: Message) -> bool:
    entities = _get_all_entities(message)
    for entity in entities:
        if entity.type == MessageEntityType.URL:
            url = entity.url or (_get_text_content(message)[entity.offset : entity.offset + entity.length])
            if url and INVITE_LINK_REGEX.search(url):
                return True
        elif entity.type == MessageEntityType.TEXT_LINK:
            if entity.url and INVITE_LINK_REGEX.search(entity.url):
                return True
    return False


def _check_botlink(message: Message) -> bool:
    entities = _get_all_entities(message)
    for entity in entities:
        if entity.type == MessageEntityType.URL:
            url = entity.url or (_get_text_content(message)[entity.offset : entity.offset + entity.length])
            if url and BOT_LINK_REGEX.search(url):
                return True
        elif entity.type == MessageEntityType.TEXT_LINK:
            if entity.url and BOT_LINK_REGEX.search(entity.url):
                return True
    return False


def _check_button(message: Message) -> bool:
    return bool(message.reply_markup)


def _check_inline(message: Message) -> bool:
    return bool(message.via_bot)


def _check_anon_channel(message: Message) -> bool:
    if not message.sender_chat:
        return False
    return message.sender_chat.type in ("channel", "supergroup")


def _check_comment(message: Message) -> bool:
    if not message.reply_to_message:
        return False
    reply = message.reply_to_message
    return bool(getattr(reply, "forum_topic_created", None) or reply.is_topic_message)


def _check_language(message: Message, lang_code: str) -> bool:
    text = _get_text_content(message)
    if not text or len(text.strip()) < 10:
        return False
    try:
        return is_text_language(text, lang_code_to_language(lang_code))
    except Exception as e:
        log.debug("Language detection error", error=str(e), lang_code=lang_code)
        return False


LOCK_TYPE_CHECKS: dict[str, Callable[[Message], bool]] = {
    LockType.ALL: lambda m: True,
    LockType.ALBUM: lambda m: bool(m.media_group_id),
    LockType.ANON_CHANNEL: _check_anon_channel,
    LockType.AUDIO: lambda m: bool(m.audio),
    LockType.BOT: lambda m: bool(m.from_user and m.from_user.is_bot),
    LockType.BOT_LINK: _check_botlink,
    LockType.BUTTON: _check_button,
    LockType.CASHTAG: lambda m: _has_entity_type(m, MessageEntityType.CASHTAG),
    LockType.CHECKLIST: lambda m: bool(getattr(m, "checklist", None)),
    LockType.CJK: _check_cjk,
    LockType.COMMAND: lambda m: _has_entity_type(m, MessageEntityType.BOT_COMMAND),
    LockType.COMMENT: _check_comment,
    LockType.CONTACT: lambda m: bool(m.contact),
    LockType.CYRILLIC: _check_cyrillic,
    LockType.DOCUMENT: lambda m: bool(m.document),
    LockType.EMAIL: lambda m: _has_entity_type(m, MessageEntityType.EMAIL),
    LockType.EMOJI: _check_emoji,
    LockType.EMOJI_CUSTOM: _check_emoji_custom,
    LockType.EMOJI_GAME: lambda m: bool(m.game),
    LockType.EMOJI_ONLY: _check_emoji_only,
    LockType.EXTERNAL_REPLY: lambda m: bool(getattr(m, "external_reply", None)),
    LockType.FORWARD: _check_forward,
    LockType.FORWARD_BOT: _check_forward_bot,
    LockType.FORWARD_CHANNEL: _check_forward_channel,
    LockType.FORWARD_STORY: _check_forward_story,
    LockType.FORWARD_USER: _check_forward_user,
    LockType.GAME: lambda m: bool(m.game),
    LockType.GIF: lambda m: bool(m.animation),
    LockType.INLINE: _check_inline,
    LockType.INVITE_LINK: _check_invite_link,
    LockType.LOCATION: lambda m: bool(m.location or m.venue),
    LockType.PHONE: lambda m: _has_entity_type(m, MessageEntityType.PHONE_NUMBER),
    LockType.PHOTO: lambda m: bool(m.photo),
    LockType.POLL: lambda m: bool(m.poll),
    LockType.RTL: _check_rtl,
    LockType.SPOILER: lambda m: _has_entity_type(m, MessageEntityType.SPOILER),
    LockType.STICKER: lambda m: bool(m.sticker),
    LockType.STICKER_ANIMATED: lambda m: bool(m.sticker and m.sticker.is_animated),
    LockType.STICKER_PREMIUM: lambda m: bool(m.sticker and m.sticker.premium_animation),
    LockType.TEXT: lambda m: (
        bool(m.text) and not any([m.photo, m.video, m.audio, m.document, m.sticker, m.animation, m.voice, m.video_note])
    ),
    LockType.URL: _check_url,
    LockType.VIDEO: lambda m: bool(m.video),
    LockType.VIDEO_NOTE: lambda m: bool(m.video_note),
    LockType.VOICE: lambda m: bool(m.voice),
    LockType.ZALGO: _check_zalgo,
    LockType.DICE: lambda m: bool(m.dice),
}


async def check_locks(message: Message, locked_types: set[str]) -> str | None:
    if not locked_types:
        return None

    if LockType.ALL in locked_types:
        return LockType.ALL

    for lock_type in locked_types:
        if is_stickerpack_lock(lock_type):
            pack_name = get_stickerpack_name(lock_type)
            if pack_name and _check_stickerpack(message, pack_name):
                return lock_type
            continue

        if is_language_lock(lock_type):
            lang_code = get_language_code(lock_type)
            if lang_code and _check_language(message, lang_code):
                return lock_type
            continue

        check_func = LOCK_TYPE_CHECKS.get(lock_type)
        if check_func:
            try:
                if check_func(message):
                    return lock_type
            except Exception as e:
                log.debug(f"Lock check error for {lock_type}: {e}")
                continue

    return None
