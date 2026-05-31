"""Lock type constants shared across modules.

Extracted from sophie_bot.modules.locks.handlers.lockable to break the
ai ↔ locks circular dependency.
"""

from __future__ import annotations

from babel.support import LazyProxy
from lingua import Language

from sophie_bot.utils.i18n import lazy_gettext as l_

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
    "guestbot": l_("Messages from guest bots (bots mentioned via @username without being chat members)"),
    "inline": l_("Messages sent via inline bots"),
    "mention": l_("Mention entities (@username)"),
    "invitelink": l_("Telegram invite links (t.me/+)"),
    "outsidereaction": l_("Reactions from users who are not members of the chat"),
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
    "webpreview": l_("Messages with web page link previews"),
    "arabic": l_("Messages containing Arabic script"),
    "hashtag": l_("Hashtag entities (#tag)"),
    "code": l_("Inline code entities (`code`)"),
    "pre": l_("Preformatted code blocks"),
    "blockquote": l_("Blockquote entities"),
    "underline": l_("Underlined text entities"),
    "strikethrough": l_("Strikethrough text entities"),
    "media": l_("Any media message (photo, video, audio, document, sticker, etc.)"),
    "edited": l_("Edited messages"),
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
    "mention",
    "botlink",
    "command",
    "spoiler",
    "emoji",
    "emojicustom",
    "emojigame",
    "emojionly",
    "button",
    "hashtag",
    "code",
    "pre",
    "blockquote",
    "underline",
    "strikethrough",
    "webpreview",
)

TEXT_PATTERN_TYPES: tuple[str, ...] = (
    "cjk",
    "cyrillic",
    "rtl",
    "arabic",
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
    "guestbot",
    "inline",
    "outsidereaction",
    "media",
    "edited",
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
