from ass_tg.exceptions import ArgStrictError
from ass_tg.types import OrArg, UserIDArg, UsernameArg
from babel.support import LazyProxy as BabelLazyProxy

from sophie_bot.db.db_exceptions import DBNotFoundException
from sophie_bot.db.models import ChatModel
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class SophieChatIDArg(UserIDArg[ChatModel]):
    @staticmethod
    async def check_type(text: str) -> bool:
        # Unlike a user ID, a chat ID is negative for groups/supergroups/channels, so the
        # inherited `isdigit()` check (which rejects the leading "-") can't be used here.
        words = text.split()
        return bool(words) and words[0].removeprefix("-").isdigit()

    async def value(self, text: str) -> ChatModel:
        chat_id = self.parse_user_id(text)

        try:
            chat = await ChatModel.get_by_tid(chat_id)
            if not chat:
                raise DBNotFoundException()
            return chat
        except DBNotFoundException:
            raise ArgStrictError(_("Could not find the requested Chat ID in the database."))


class SophieChatUsernameArg(UsernameArg[ChatModel]):
    async def value(self, text: str) -> ChatModel:
        username = self.parse_username(text)

        try:
            chat = await ChatModel.find_one(ChatModel.username == username)
            if not chat:
                raise DBNotFoundException()
            return chat
        except DBNotFoundException:
            raise ArgStrictError(_("Could not find the requested Username in the database."))


class SophieChatArg(OrArg):
    def __init__(self, *args):
        description = args[0] if args else None
        super().__init__(
            SophieChatIDArg(),
            SophieChatUsernameArg(),
            description=description,
        )

    @staticmethod
    def needed_type() -> tuple[LazyProxy, LazyProxy]:
        return l_("Chat: 'Chat ID (numeric) / Username (starts with @)'"), l_(
            "Chats: 'Chat IDs (numeric) / Usernames (start with @)'"
        )

    @property
    def examples(self) -> dict[str, BabelLazyProxy | None] | None:
        return {
            "-100123456789": l_("Chat ID"),
            "@SophieChat": l_("Username"),
        }
