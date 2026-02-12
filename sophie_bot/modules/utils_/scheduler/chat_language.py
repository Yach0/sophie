from beanie import PydanticObjectId

from sophie_bot.db.models import LanguageModel
from sophie_bot.services.i18n import i18n


class UseChatLanguage:
    chat_iid: PydanticObjectId

    def __init__(self, chat_iid: PydanticObjectId):
        self.chat_iid = chat_iid

    async def __aenter__(self):
        chat_language = await LanguageModel.get_locale(self.chat_iid)

        self.ctx_token = i18n.ctx_locale.set(chat_language)
        self.token = i18n.set_current(i18n)

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        i18n.ctx_locale.reset(self.ctx_token)
        i18n.reset_current(self.token)

        return True
