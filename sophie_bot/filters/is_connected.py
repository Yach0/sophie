from typing import Any

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.filters import Filter
from aiogram.types import Message
from stfu_tg import Doc

from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.utils.i18n import gettext as _


class IsConnectedFilter(Filter):
    def __init__(self, allow_abort: bool = True):
        self.allow_abort = allow_abort
        super().__init__()

    async def __call__(
        self, message: Message, *args: Any, connection: ChatConnection | None = None, **kwargs: Any
    ) -> bool | dict[str, Any]:
        return bool(connection and connection.is_connected and message.from_user)


class GroupOrConnectedFilter(Filter):
    def __init__(self, allow_abort: bool = True, require_admin: bool = False):
        self.allow_abort = allow_abort
        self.require_admin = require_admin
        super().__init__()

    """Filters cases when it's a group or connected to a group in PM."""

    async def __call__(self, message: Message, *args: Any, **kwargs: Any) -> bool | dict[str, Any]:
        if await ChatTypeFilter(ChatType.GROUP, ChatType.SUPERGROUP)(message, *args, **kwargs):
            return True

        if await IsConnectedFilter(self.allow_abort)(message, *args, **kwargs):
            return True

        if self.allow_abort:
            await message.reply(
                Doc(
                    _("This command can only be used in a group or while being connected to a group in PM."),
                    _("Refer to /help for more information about connections."),
                ).to_html()
            )
            raise SkipHandler

        return False
