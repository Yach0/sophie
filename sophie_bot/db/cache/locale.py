from beanie import PydanticObjectId

from sophie_bot.constants import CACHE_LANGUAGE_TTL_SECONDS
from sophie_bot.db.models import LanguageModel
from sophie_bot.utils.cached import cached


@cached(ttl=CACHE_LANGUAGE_TTL_SECONDS)
async def cache_get_locale_name(chat_iid: PydanticObjectId) -> str | bool:
    model = await LanguageModel.find_one(LanguageModel.chat.id == chat_iid)

    return model.lang if model else False
