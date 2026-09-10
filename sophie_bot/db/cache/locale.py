"""Explicitly owned selected-locale store backed by the canonical ``lang`` collection."""

from __future__ import annotations

from beanie import PydanticObjectId
from beanie.odm.operators.update.general import Set

from sophie_bot.constants import CACHE_LANGUAGE_TTL_SECONDS
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.language import LanguageModel
from sophie_bot.utils.cached import CachedFunction, RedisCache, cached
from sophie_bot.utils.i18n import I18nNew

_CACHE_KEY = "sophie_bot.db.cache.locale:_cached_stored_locale"


class LocaleStore:
    def __init__(self, cache: RedisCache, i18n: I18nNew, default_locale: str) -> None:
        self.cache = cache
        self.i18n = i18n
        self.default_locale = default_locale
        self._cached_stored_locale: CachedFunction[[PydanticObjectId], str | None] = cached(
            cache,
            ttl=CACHE_LANGUAGE_TTL_SECONDS,
            key=_CACHE_KEY,
        )(self._load_stored_locale)

    @staticmethod
    async def _load_stored_locale(chat_iid: PydanticObjectId) -> str | None:
        model = await LanguageModel.find_one(LanguageModel.chat.id == chat_iid)
        return model.lang if model else None

    async def get_selected_locale(self, chat_iid: PydanticObjectId) -> str | None:
        locale_name = await self._cached_stored_locale(chat_iid)
        return locale_name if locale_name in self.i18n.available_locales else None

    async def get_chat_locale(self, chat_iid: PydanticObjectId) -> str:
        return await self.get_selected_locale(chat_iid) or self.default_locale

    async def set_selected_locale(self, chat: ChatModel, locale_name: str) -> None:
        await LanguageModel.find_one(LanguageModel.chat.id == chat.iid).upsert(
            Set({LanguageModel.lang: locale_name}),
            on_insert=LanguageModel(chat=chat, lang=locale_name),
        )
        await self._cached_stored_locale.reset_cache(chat.iid)
