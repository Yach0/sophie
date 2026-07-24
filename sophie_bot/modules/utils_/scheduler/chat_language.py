from types import TracebackType
from typing import Self

from beanie import PydanticObjectId

from sophie_bot.db.cache.locale import get_chat_locale
from sophie_bot.services.i18n import i18n


class UseChatLanguage:
    """Render everything inside the block in a chat's language."""

    chat_iid: PydanticObjectId

    def __init__(self, chat_iid: PydanticObjectId):
        self.chat_iid = chat_iid

    async def __aenter__(self) -> Self:
        chat_language = await get_chat_locale(self.chat_iid)

        self.ctx_token = i18n.ctx_locale.set(chat_language)
        self.token = i18n.set_current(i18n)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # Returning None (not True): restoring the locale must not decide whether the
        # caller's exception is worth seeing.
        i18n.ctx_locale.reset(self.ctx_token)
        i18n.reset_current(self.token)
