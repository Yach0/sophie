from beanie import Document

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class LanguageModel(Document):
    """A chat's explicitly selected language. Read and written via sophie_bot.db.cache.locale."""

    chat: Link[ChatModel]

    lang: str

    class Settings:
        name = "lang"
