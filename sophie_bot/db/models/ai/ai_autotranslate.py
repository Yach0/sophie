from beanie import Document, PydanticObjectId
from pydantic import Field

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class AIAutotranslateModel(Document):
    chat: Link[ChatModel]
    excluded_languages: set[str] = Field(default_factory=set)

    class Settings:
        name = "ai_autotranslate"

    @staticmethod
    async def get_state(chat_id: PydanticObjectId) -> bool:
        return bool(await AIAutotranslateModel.find_one(AIAutotranslateModel.chat.id == chat_id))

    @staticmethod
    async def set_state(chat: "ChatModel", new_state: bool):
        model = await AIAutotranslateModel.find_one(AIAutotranslateModel.chat.id == chat.iid)
        if model and not new_state:
            return await model.delete()

        if model:
            return model

        model = AIAutotranslateModel(chat=chat)
        return await model.save()

    @staticmethod
    async def get_excluded_languages(chat_id: PydanticObjectId) -> set[str]:
        model = await AIAutotranslateModel.find_one(AIAutotranslateModel.chat.id == chat_id)
        return model.excluded_languages if model else set()

    @staticmethod
    async def toggle_excluded_language(chat: "ChatModel", language_code: str) -> set[str]:
        model = await AIAutotranslateModel.find_one(AIAutotranslateModel.chat.id == chat.iid)
        if not model:
            model = AIAutotranslateModel(chat=chat)
        if language_code in model.excluded_languages:
            model.excluded_languages.remove(language_code)
        else:
            model.excluded_languages.add(language_code)
        await model.save()
        return model.excluded_languages
