from __future__ import annotations

from random import sample
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from babel.support import LazyProxy
from stfu_tg import BlockQuote, Code, Doc, KeyValue, Section, Template, Title, VList
from stfu_tg.doc import Element

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.locks.utils.lock_types import (
    get_language_code,
    get_stickerpack_name,
    is_language_lock,
    is_stickerpack_lock,
)
from sophie_bot.shared.lock_constants import (
    CONTENT_TYPES as CONTENT_TYPES,
)
from sophie_bot.shared.lock_constants import (
    ENTITY_TYPES as ENTITY_TYPES,
)
from sophie_bot.shared.lock_constants import (
    FORWARD_TYPES as FORWARD_TYPES,
)
from sophie_bot.shared.lock_constants import (
    LOCK_TYPE_DESCRIPTIONS as LOCK_TYPE_DESCRIPTIONS,
)
from sophie_bot.shared.lock_constants import (
    SPECIAL_TYPES as SPECIAL_TYPES,
)
from sophie_bot.shared.lock_constants import (
    STICKER_PACK_TYPES as STICKER_PACK_TYPES,
)
from sophie_bot.shared.lock_constants import (
    SUPPORTED_LANGUAGES as SUPPORTED_LANGUAGES,
)
from sophie_bot.shared.lock_constants import (
    TEXT_PATTERN_TYPES as TEXT_PATTERN_TYPES,
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
    "mention": l_("Mentions"),
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


def build_lockable_sections(full_languages: bool = False) -> tuple[Section, ...]:
    language_items: list[Any]
    if full_languages:
        language_items = [
            *[KeyValue(Code(f"language:{code}"), name) for code, name in sorted(SUPPORTED_LANGUAGES.items())],
        ]
    else:
        language_items = [
            *sample([KeyValue(Code(f"language:{code}"), name) for code, name in SUPPORTED_LANGUAGES.items()], 5),
            Template(_("To see all supported languages, use {cmd}"), cmd=Code("/locklanguages")),
        ]

    return (
        Section(
            _build_lock_list(CONTENT_TYPES),
            title=_("Media types"),
        ),
        Section(
            _build_lock_list(ENTITY_TYPES),
            title=_("Entities and links"),
        ),
        Section(
            _build_lock_list(FORWARD_TYPES),
            title=_("Forwards"),
        ),
        Section(
            _build_lock_list(TEXT_PATTERN_TYPES),
            title=_("Text patterns"),
        ),
        Section(
            _build_lock_list(STICKER_PACK_TYPES),
            VList(KeyValue(Code("stickerpack:PACK_ID"), _("Lock a specific sticker pack by its ID"))),
            title=_("Sticker types"),
        ),
        Section(
            VList(*language_items),
            title=_("Languages"),
        ),
        Section(
            _build_lock_list(SPECIAL_TYPES),
            title=_("Special"),
        ),
    )


def build_lockable_chat_sections(full_languages: bool = False) -> tuple[Element, ...]:
    return tuple(
        BlockQuote(section, expandable=True) for section in build_lockable_sections(full_languages=full_languages)
    )


def build_lockable_doc(full_languages: bool = False) -> Doc:
    return Doc(
        Title(_("Available lock types")),
        *build_lockable_chat_sections(full_languages=full_languages),
        Template(
            _("Use {cmd} to lock a specific type."),
            cmd=Code("/lock <type>"),
        ),
    )


@flags.help(description=l_("Shows all lockable message types"))
@flags.disableable(name="lockable")
class ListLockableHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("lockable", "locktypes")),)

    async def handle(self) -> Any:
        message: Message = self.event
        await message.reply(build_lockable_doc().to_html())
