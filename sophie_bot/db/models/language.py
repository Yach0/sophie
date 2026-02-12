from beanie import Document, PydanticObjectId

from sophie_bot.config import CONFIG
from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class LanguageModel(Document):
    chat: Link[ChatModel]

    lang: str

    class Settings:
        name = "lang"

    @staticmethod
    async def get_locale(chat_iid: PydanticObjectId) -> str:
        item = await LanguageModel.find_one(LanguageModel.chat.id == chat_iid)
        return item.lang if item else CONFIG.default_locale
