"""The single source of truth for which locale a chat is rendered in.

The `lang` collection (`LanguageModel`) holds the language a chat explicitly selected via
`/lang`. `ChatModel.language_code` is *not* a selection: it mirrors whatever locale the
Telegram client reports, and is overwritten on every private message.

Every reader goes through `get_selected_locale`/`get_chat_locale` and every writer through
`set_selected_locale`, which invalidates the cache as part of the write.
"""

from beanie import PydanticObjectId
from beanie.odm.operators.update.general import Set

from sophie_bot.config import CONFIG
from sophie_bot.constants import CACHE_LANGUAGE_TTL_SECONDS
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.language import LanguageModel
from sophie_bot.services.i18n import i18n
from sophie_bot.utils.cached import cached


@cached(ttl=CACHE_LANGUAGE_TTL_SECONDS)
async def _cached_stored_locale(chat_iid: PydanticObjectId) -> str | None:
    """The raw stored selection for a chat, or None when the chat never selected one.

    Cached on the hot path: `LocalizationMiddleware` resolves a locale for every update.
    Validation deliberately happens outside the cache so that a locale being dropped from
    the build takes effect immediately instead of after the TTL.
    """
    model = await LanguageModel.find_one(LanguageModel.chat.id == chat_iid)

    return model.lang if model else None


async def get_selected_locale(chat_iid: PydanticObjectId) -> str | None:
    """The locale a chat selected, or None when it has no usable selection."""
    locale_name = await _cached_stored_locale(chat_iid)

    return locale_name if locale_name in i18n.available_locales else None


async def get_chat_locale(chat_iid: PydanticObjectId) -> str:
    """The locale a chat's messages should be rendered in."""
    return await get_selected_locale(chat_iid) or CONFIG.default_locale


async def set_selected_locale(chat: ChatModel, locale_name: str) -> None:
    """Persist a chat's selected locale and drop the now-stale cache entry."""
    await LanguageModel.find_one(LanguageModel.chat.id == chat.iid).upsert(
        Set({LanguageModel.lang: locale_name}),
        on_insert=LanguageModel(chat=chat, lang=locale_name),
    )

    await _cached_stored_locale.reset_cache(chat.iid)
