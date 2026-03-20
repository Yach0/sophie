from __future__ import annotations

from random import sample
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from babel.support import LazyProxy
from lingua import Language
from stfu_tg import BlockQuote, Code, Doc, KeyValue, Section, Template, Title, VList

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.locks.utils.lock_types import (
    get_language_code,
    get_stickerpack_name,
    is_language_lock,
    is_stickerpack_lock,
)
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

LOCK_TYPE_DISPLAY_NAMES: dict[str, LazyProxy] = {
    "all": l_("All messages"),
    "album": l_("Media albums"),
    "anonchannel": l_("Anonymous channels"),
    "audio": l_("Audio files"),
    "bot": l_("Bot messages"),
    "botlink": l_("Bot links"),
    "button": l_("Inline buttons"),
    "cashtag": l_("Cashtags ($TAG)"),
    "checklist": l_("Checklists"),
    "cjk": l_("CJK characters"),
    "command": l_("Commands"),
    "comment": l_("Comments"),
    "contact": l_("Contacts"),
    "cyrillic": l_("Cyrillic text"),
    "document": l_("Documents"),
    "email": l_("Email addresses"),
    "emoji": l_("Emoji"),
    "emojicustom": l_("Custom emoji"),
    "emojigame": l_("Game messages"),
    "emojionly": l_("Emoji-only messages"),
    "externalreply": l_("External replies"),
    "forward": l_("Forwarded messages"),
    "forwardbot": l_("Bot forwards"),
    "forwardchannel": l_("Channel forwards"),
    "forwardstory": l_("Story forwards"),
    "forwarduser": l_("User forwards"),
    "game": l_("Games"),
    "gif": l_("GIFs"),
    "inline": l_("Inline results"),
    "invitelink": l_("Invite links"),
    "location": l_("Locations"),
    "phone": l_("Phone numbers"),
    "photo": l_("Photos"),
    "poll": l_("Polls"),
    "rtl": l_("RTL text"),
    "spoiler": l_("Spoilers"),
    "sticker": l_("Stickers"),
    "stickeranimated": l_("Animated stickers"),
    "stickerpremium": l_("Premium stickers"),
    "text": l_("Text messages"),
    "url": l_("URLs"),
    "video": l_("Videos"),
    "videonote": l_("Video notes"),
    "voice": l_("Voice messages"),
    "zalgo": l_("Zalgo text"),
    "dice": l_("Dice"),
}

LOCK_TYPE_DESCRIPTIONS: dict[str, LazyProxy] = {
    "all": l_("Blocks all message types"),
    "album": l_("Messages with media albums (grouped photos/videos)"),
    "anonchannel": l_("Messages sent on behalf of a channel anonymously"),
    "audio": l_("Messages with audio files"),
    "bot": l_("Messages from bot accounts"),
    "botlink": l_("Links to Telegram bots (t.me/botname)"),
    "button": l_("Messages with inline keyboard buttons"),
    "cashtag": l_("Cashtag entities ($TICKER)"),
    "checklist": l_("Messages with checklists"),
    "cjk": l_("Messages containing CJK characters (Chinese, Japanese, Korean)"),
    "command": l_("Bot command entities (/command@bot)"),
    "comment": l_("Messages sent as comments in channels"),
    "contact": l_("Messages with shared contacts"),
    "cyrillic": l_("Messages containing Cyrillic characters"),
    "document": l_("Messages with files/documents"),
    "email": l_("Email address entities"),
    "emoji": l_("Messages containing emoji"),
    "emojicustom": l_("Custom emoji entities"),
    "emojigame": l_("Game messages with emoji"),
    "emojionly": l_("Messages containing only emoji"),
    "externalreply": l_("External reply references"),
    "forward": l_("All forwarded messages"),
    "forwardbot": l_("Messages forwarded from bots"),
    "forwardchannel": l_("Messages forwarded from channels"),
    "forwardstory": l_("Messages forwarded from stories"),
    "forwarduser": l_("Messages forwarded from users"),
    "game": l_("Messages with Telegram games"),
    "gif": l_("Messages with GIF animations"),
    "inline": l_("Messages sent via inline bots"),
    "invitelink": l_("Telegram invite links (t.me/+)"),
    "location": l_("Messages with location or venue"),
    "phone": l_("Phone number entities"),
    "photo": l_("Messages with photos"),
    "poll": l_("Messages with polls or quizzes"),
    "rtl": l_("Messages containing RTL (right-to-left) text"),
    "spoiler": l_("Spoiler text entities"),
    "sticker": l_("Messages with stickers"),
    "stickeranimated": l_("Animated stickers"),
    "stickerpremium": l_("Premium animated stickers"),
    "text": l_("Text-only messages without media"),
    "url": l_("URL entities in messages"),
    "video": l_("Messages with videos"),
    "videonote": l_("Messages with video notes (round videos)"),
    "voice": l_("Messages with voice recordings"),
    "zalgo": l_("Messages with excessive formatting characters (glitch text)"),
    "dice": l_("Messages with dice rolls"),
}

CONTENT_TYPES: tuple[str, ...] = (
    "audio",
    "document",
    "gif",
    "photo",
    "video",
    "videonote",
    "voice",
    "sticker",
    "contact",
    "location",
    "poll",
    "game",
    "text",
    "dice",
    "checklist",
    "album",
)

ENTITY_TYPES: tuple[str, ...] = (
    "url",
    "email",
    "phone",
    "cashtag",
    "invitelink",
    "botlink",
    "command",
    "spoiler",
    "emoji",
    "emojicustom",
    "emojigame",
    "emojionly",
    "button",
)

TEXT_PATTERN_TYPES: tuple[str, ...] = (
    "cjk",
    "cyrillic",
    "rtl",
    "zalgo",
)

FORWARD_TYPES: tuple[str, ...] = (
    "forward",
    "forwardbot",
    "forwardchannel",
    "forwardstory",
    "forwarduser",
    "externalreply",
)

STICKER_PACK_TYPES: tuple[str, ...] = (
    "stickeranimated",
    "stickerpremium",
)

SPECIAL_TYPES: tuple[str, ...] = (
    "all",
    "bot",
    "anonchannel",
    "comment",
    "inline",
)


def _get_supported_languages() -> dict[str, str]:
    languages: dict[str, str] = {}
    for attr_name in dir(Language):
        if attr_name.isupper() and not attr_name.startswith("_"):
            lang = getattr(Language, attr_name)
            iso_code = lang.iso_code_639_1
            if iso_code:
                code = iso_code.name.lower()
                name = attr_name.title()
                languages[code] = name
    return languages


SUPPORTED_LANGUAGES: dict[str, str] = _get_supported_languages()


def get_lock_description(lock_type: str) -> LazyProxy | Template | str:
    if is_stickerpack_lock(lock_type):
        pack_name = get_stickerpack_name(lock_type) or "unknown"
        return Template(_("Sticker pack: {pack}"), pack=pack_name)
    if is_language_lock(lock_type):
        lang_code = get_language_code(lock_type) or "unknown"
        lang_name = SUPPORTED_LANGUAGES.get(lang_code, lang_code)
        return Template(_("Messages in {lang} language"), lang=lang_name)

    return LOCK_TYPE_DESCRIPTIONS.get(lock_type, lock_type)


def get_lock_display_name(lock_type: str) -> KeyValue:
    description = get_lock_description(lock_type)
    return KeyValue(Code(lock_type), description)


def _build_lock_list(lock_types: tuple[str, ...]) -> VList:
    return VList(*[get_lock_display_name(lock_type) for lock_type in lock_types])


@flags.help(description=l_("Shows all lockable message types"))
@flags.disableable(name="lockable")
class ListLockableHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("lockable", "locktypes")),)

    async def handle(self) -> Any:
        message: Message = self.event

        doc = Doc(
            Title(_("Available lock types")),
            BlockQuote(
                Section(
                    _build_lock_list(CONTENT_TYPES),
                    title=_("Media types"),
                ),
                expandable=True,
            ),
            BlockQuote(
                Section(
                    _build_lock_list(ENTITY_TYPES),
                    title=_("Entities and links"),
                ),
                expandable=True,
            ),
            BlockQuote(
                Section(
                    _build_lock_list(FORWARD_TYPES),
                    title=_("Forwards"),
                ),
                expandable=True,
            ),
            BlockQuote(
                Section(
                    _build_lock_list(TEXT_PATTERN_TYPES),
                    title=_("Text patterns"),
                ),
                expandable=True,
            ),
            BlockQuote(
                Section(
                    _build_lock_list(STICKER_PACK_TYPES),
                    VList(KeyValue(Code("stickerpack:PACK_ID"), _("Lock a specific sticker pack by its ID"))),
                    title=_("Sticker types"),
                ),
                expandable=True,
            ),
            BlockQuote(
                Section(
                    VList(
                        *sample(
                            [KeyValue(Code(f"language:{code}"), name) for code, name in SUPPORTED_LANGUAGES.items()], 5
                        ),
                        Template(_("To see all supported languages, use {cmd}"), cmd=Code("/locklanguages")),
                    ),
                    title=_("Languages"),
                ),
                expandable=True,
            ),
            BlockQuote(
                Section(
                    _build_lock_list(SPECIAL_TYPES),
                    title=_("Special"),
                ),
                expandable=True,
            ),
            Template(
                _("Use {cmd} to lock a specific type."),
                cmd=Code("/lock <type>"),
            ),
        )

        await message.reply(doc.to_html())
