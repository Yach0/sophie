"""Migration: copy_selected_locale_to_lang_collection

Description:
    Restores the `lang` collection as the store for a chat's selected language.

    Sophie 4.0 moved the /lang write from `lang` onto `chats.language_code`, a field that
    also mirrors the locale reported by the user's Telegram client and is overwritten on
    every private message. This copies each surviving selection back into `lang` so the
    canonical store is populated before the code starts reading it.

    Only values that are real Sophie locales (present in i18n.available_locales, e.g.
    "ru_RU") are copied. Client-reported values such as "en" or "ru" are not selections a
    user ever made through /lang, and copying them would fabricate one.

    `chats.language_code` is deliberately left populated: it is once again the
    client-reported field, and nothing reads it to select a locale any more. A group row
    left holding a stale "ru_RU" is inert -- do not "tidy" it away.

Affected Collections:
    - chats (read only)
    - lang (inserts)

Impact:
    - Low risk; purely additive, and idempotent (chats that already have a `lang` document
      are skipped, so a re-run never overwrites a newer selection).
    - Small collection: only chats whose language_code is a valid Sophie locale.
    - Backward is a no-op; see the Backward docstring.
"""

from __future__ import annotations

from beanie import free_fall_migration
from beanie.odm.operators.find.comparison import In

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.language import LanguageModel
from sophie_bot.services.i18n import i18n
from sophie_bot.utils.logger import log


async def copy_selected_locales(available_locales: tuple[str, ...]) -> int:
    """Copy valid selected locales from chats.language_code into the lang collection.

    Returns the number of documents inserted. Kept as a plain function so tests can drive it.
    """
    copied = 0

    async for chat in ChatModel.find(In(ChatModel.language_code, list(available_locales))):
        locale_name = chat.language_code
        if locale_name is None:
            continue

        if await LanguageModel.find_one(LanguageModel.chat.id == chat.iid):
            continue

        await LanguageModel(chat=chat, lang=locale_name).insert()
        copied += 1

    if copied:
        log.info("Copied selected locales into the lang collection", copied=copied)

    return copied


class Forward:
    """Copy each chat's selected language from chats.language_code into the lang collection."""

    @free_fall_migration(document_models=[ChatModel, LanguageModel])
    async def copy_locales(self, session) -> None:
        del session
        await copy_selected_locales(i18n.available_locales)


class Backward:
    """No rollback.

    Forward inserts a `lang` document only for chats that had none, so afterwards its own set
    is indistinguishable from a selection a user made through /lang once this migration had
    run: both are a `lang` document whose value may equal the chat's language_code. A Backward
    that deleted on that shape would revert more than Forward changed.

    Leaving the documents in place is safe. On a rollback the old code reads
    chats.language_code, which Forward never modified, and ignores the lang collection
    entirely -- so the copies are inert rather than wrong.
    """

    @free_fall_migration(document_models=[ChatModel, LanguageModel])
    async def noop(self, session) -> None:
        del session
