"""Tests for the canonical selected-locale store (the `lang` collection).

Regression coverage for two findings:
  - #32: a private chat's selected language was stored on ChatModel.language_code, which the
    per-message user upsert overwrites with the Telegram client's reported locale.
  - #43: the fed-unban and scheduler readers read the `lang` collection, which /lang had
    stopped writing, so they always fell back to the default locale.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from aiogram.types import User
from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import In

from sophie_bot.config import CONFIG
from sophie_bot.db.cache.locale import LocaleStore
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.language import LanguageModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.middlewares.localization import LocalizationMiddleware, _locale_for_language
from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.modules.language.handlers.language import LanguageCallbackHandler, SelectLangCb
from sophie_bot.modules.utils_.scheduler.chat_language import UseChatLanguage
from sophie_bot.services.i18n import i18n
from sophie_bot.utils.cached import RedisCache

_CLIENT_LOCALE = "en"  # What Telegram reports; not a Sophie locale directory
_SELECTED_LOCALE = "ru_RU"


def _telegram_user(user_tid: int, language_code: str | None = _CLIENT_LOCALE) -> User:
    return User(
        id=user_tid,
        is_bot=False,
        first_name="Tester",
        username=f"tester{user_tid}",
        language_code=language_code,
    )


class _ChatFactory:
    """Creates chats and removes exactly those again afterwards.

    `db_init` is session scoped, so the chats collection is shared with every other test in
    this xdist worker and must never be wiped wholesale. The migration tests also scan every
    chat, so they need the chats they did not create to be gone by the time they run.
    """

    def __init__(self) -> None:
        self._iids: list[PydanticObjectId] = []

    async def group(self, chat_tid: int, language_code: str | None = None) -> ChatModel:
        await ChatModel(
            tid=chat_tid,
            type=ChatType.supergroup,
            first_name_or_title="Test group",
            username=None,
            is_bot=False,
            language_code=language_code,
            last_saw=datetime.now(UTC),
        ).insert()

        chat = await ChatModel.get_by_tid(chat_tid)
        assert chat  # Re-read: ChatModel.iid is only trustworthy on a document loaded from the DB
        self._iids.append(chat.iid)
        return chat

    async def user(self, user_tid: int, language_code: str | None = _CLIENT_LOCALE) -> ChatModel:
        await ChatModel.upsert_user(_telegram_user(user_tid, language_code))

        chat = await ChatModel.get_by_tid(user_tid)
        assert chat
        self._iids.append(chat.iid)
        return chat

    async def cleanup(self) -> None:
        await LanguageModel.find(In(LanguageModel.chat.id, self._iids)).delete()
        await ChatModel.find(In(ChatModel.iid, self._iids)).delete()


@pytest.fixture
async def chats(db_init: Any) -> AsyncGenerator[_ChatFactory]:
    factory = _ChatFactory()

    yield factory

    await factory.cleanup()


@pytest.fixture
def locales(test_redis: object) -> LocaleStore:
    return LocaleStore(
        RedisCache(test_redis),
        i18n,
        CONFIG.default_locale,
    )

class _FakeMessage:
    def __init__(self) -> None:
        self.edited: list[str] = []

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edited.append(text)


class _FakeCallbackQuery:
    """Only the surface LanguageCallbackHandler touches."""

    def __init__(self) -> None:
        self.from_user = _telegram_user(1)
        self.message = _FakeMessage()
        self.answers: list[str | None] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append(text)


async def _select_language_via_handler(
    chat: ChatModel,
    locale_name: str,
    locales: LocaleStore,
) -> _FakeCallbackQuery:
    """Drive the real /lang callback handler against a connected chat.

    Runs inside the real i18n context: the session fixture in conftest builds an I18nNew on
    aiogram's default "messages" domain, which finds none of Sophie's catalogs, so the
    handler's own available_locales check would reject every locale.
    """
    event = _FakeCallbackQuery()
    connection = ChatConnection(
        type=chat.type,
        is_connected=chat.type is not ChatType.private,
        tid=chat.tid,
        title=chat.first_name_or_title,
        db_model=chat,
    )
    handler = LanguageCallbackHandler(
        event,
        context=RequestContext(connection=connection),
        services=SimpleNamespace(locales=locales),
        callback_data=SelectLangCb(code=locale_name),
    )

    with i18n.context():
        await handler.handle()

    return event


# --- Finding #32: the client-reported locale must not clobber the selection ---


async def test_selection_survives_a_private_message_upsert(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    user_tid = 5551
    user = await chats.user(user_tid)

    await _select_language_via_handler(user, _SELECTED_LOCALE, locales)

    # A later DM re-upserts the user, overwriting language_code with the client's locale
    refreshed = await ChatModel.upsert_user(_telegram_user(user_tid))
    assert refreshed.language_code == _CLIENT_LOCALE

    middleware = LocalizationMiddleware(locales)
    locale = await middleware.get_locale(
        SimpleNamespace(from_user=_telegram_user(user_tid)),
        {"context": RequestContext(event_chat=refreshed)},
    )

    assert locale == _SELECTED_LOCALE


# --- Finding #43: the orphaned readers must see the selection ---


async def test_scheduler_and_fed_unban_readers_see_the_selection(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    chat = await chats.group(-100777)
    await _select_language_via_handler(chat, _SELECTED_LOCALE, locales)

    # The path fed-unban uses to format its ban date
    assert await locales.get_chat_locale(chat.iid) == _SELECTED_LOCALE

    # The path UseChatLanguage sets i18n.ctx_locale from. Read inside, assert outside:
    # __aexit__ returns True, so an assertion raised inside the block would be swallowed.
    async with UseChatLanguage(chat.iid, locales=locales):
        scheduler_locale = i18n.ctx_locale.get()

    assert scheduler_locale == _SELECTED_LOCALE


async def test_get_chat_locale_defaults_when_no_selection(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    chat = await chats.group(-100778)

    assert await locales.get_chat_locale(chat.iid) == CONFIG.default_locale


# --- Validation: an unknown locale must never reach gettext ---


async def test_unknown_stored_locale_is_rejected(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    chat = await chats.group(-100779)
    await LanguageModel(chat=chat, lang="not_a_locale").insert()

    assert await locales.get_selected_locale(chat.iid) is None
    assert await locales.get_chat_locale(chat.iid) == CONFIG.default_locale


async def test_middleware_rejects_a_client_locale_stored_as_a_selection(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    """A bare client value like "en" has no locale directory; it must not be returned."""
    chat = await chats.group(-100780)
    await LanguageModel(chat=chat, lang=_CLIENT_LOCALE).insert()

    middleware = LocalizationMiddleware(locales)
    locale = await middleware.get_locale(
        SimpleNamespace(from_user=None),
        {"context": RequestContext(event_chat=chat)},
    )

    assert locale == CONFIG.default_locale


async def test_middleware_falls_back_to_client_locale_in_private_chats(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    user = await chats.user(5552, language_code="uk_UA")

    middleware = LocalizationMiddleware(locales)
    locale = await middleware.get_locale(
        SimpleNamespace(
            from_user=_telegram_user(5552, language_code="uk_UA")
        ),
        {"context": RequestContext(event_chat=user)},
    )

    assert locale == "uk_UA"


@pytest.mark.parametrize(
    ("language_code", "expected_locale"),
    (("uk", "uk_UA"), ("ru-RU", "ru_RU"), ("en", "en_US")),
)
def test_locale_fallback_normalizes_telegram_language_codes(language_code: str, expected_locale: str) -> None:
    available_locales = ("en_GB", "en_US", "ru_RU", "uk_UA")

    assert _locale_for_language(language_code, available_locales) == expected_locale


# --- Cache behaviour ---


async def test_selection_change_is_visible_immediately(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    chat = await chats.group(-100781)

    await locales.set_selected_locale(chat, _SELECTED_LOCALE)
    assert await locales.get_selected_locale(chat.iid) == _SELECTED_LOCALE

    await locales.set_selected_locale(chat, "uk_UA")

    assert await locales.get_selected_locale(chat.iid) == "uk_UA"


async def test_cached_miss_is_invalidated_by_a_later_write(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    """A chat read before it had any selection must not stay cached as "no selection"."""
    chat = await chats.group(-100782)

    assert await locales.get_selected_locale(chat.iid) is None

    await locales.set_selected_locale(chat, _SELECTED_LOCALE)

    assert await locales.get_selected_locale(chat.iid) == _SELECTED_LOCALE


async def test_different_chats_do_not_share_a_cache_entry(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    first = await chats.group(-100783)
    second = await chats.group(-100784)

    await locales.set_selected_locale(first, _SELECTED_LOCALE)
    await locales.set_selected_locale(second, "uk_UA")

    assert await locales.get_selected_locale(first.iid) == _SELECTED_LOCALE
    assert await locales.get_selected_locale(second.iid) == "uk_UA"




# --- /lang operates on the connected chat ---


async def test_lang_while_connected_sets_the_group_language(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    user = await chats.user(5553)
    group = await chats.group(-100785)

    # The connection middleware hands the handler the *connected* chat
    await _select_language_via_handler(group, _SELECTED_LOCALE, locales)

    assert await locales.get_selected_locale(group.iid) == _SELECTED_LOCALE
    assert await locales.get_selected_locale(user.iid) is None


async def test_lang_reselecting_the_active_language_does_not_re_edit(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    chat = await chats.group(-100786)
    await locales.set_selected_locale(chat, _SELECTED_LOCALE)

    event = await _select_language_via_handler(
        chat,
        _SELECTED_LOCALE,
        locales,
    )

    assert event.message.edited == []


# --- Migration ---


def _locale_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260715_225629_copy_selected_locale_to_lang_collection")


async def test_migration_copies_valid_locales_only(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    migration = _locale_migration()

    selected = await chats.group(-100787, language_code=_SELECTED_LOCALE)
    client_reported = await chats.group(-100788, language_code=_CLIENT_LOCALE)
    bare_language = await chats.group(-100789, language_code="ru")
    never_set = await chats.group(-100790)

    copied = await migration.copy_selected_locales(i18n.available_locales)

    assert copied == 1
    assert await locales.get_selected_locale(selected.iid) == _SELECTED_LOCALE
    assert await LanguageModel.find_one(LanguageModel.chat.id == client_reported.iid) is None
    assert await LanguageModel.find_one(LanguageModel.chat.id == bare_language.iid) is None
    assert await LanguageModel.find_one(LanguageModel.chat.id == never_set.iid) is None


async def test_migration_does_not_overwrite_an_existing_selection(
    chats: _ChatFactory,
    locales: LocaleStore,
) -> None:
    migration = _locale_migration()

    chat = await chats.group(-100791, language_code=_SELECTED_LOCALE)
    await LanguageModel(chat=chat, lang="uk_UA").insert()

    copied = await migration.copy_selected_locales(i18n.available_locales)

    assert copied == 0
    assert await LanguageModel.find(LanguageModel.chat.id == chat.iid).count() == 1
    assert await locales.get_selected_locale(chat.iid) == "uk_UA"


async def test_migration_is_idempotent(chats: _ChatFactory) -> None:
    migration = _locale_migration()

    await chats.group(-100792, language_code=_SELECTED_LOCALE)

    assert await migration.copy_selected_locales(i18n.available_locales) == 1
    assert await migration.copy_selected_locales(i18n.available_locales) == 0


async def test_migration_leaves_language_code_populated(chats: _ChatFactory) -> None:
    """language_code is the client-reported field now; the migration must not clear it."""
    migration = _locale_migration()

    chat = await chats.group(-100793, language_code=_SELECTED_LOCALE)
    await migration.copy_selected_locales(i18n.available_locales)

    refreshed = await ChatModel.get_by_tid(chat.tid)
    assert refreshed
    assert refreshed.language_code == _SELECTED_LOCALE


async def test_migration_backward_reverts_nothing(chats: _ChatFactory) -> None:
    """Backward is an explicit no-op; assert it does not delete selections it did not create."""
    migration = _locale_migration()

    chat = await chats.group(-100794, language_code=_SELECTED_LOCALE)
    await migration.copy_selected_locales(i18n.available_locales)

    await migration.Backward.noop.run(session=None)

    assert await LanguageModel.find(LanguageModel.chat.id == chat.iid).count() == 1
