from typing import Any, Callable, Dict, Optional, Union

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Filter
from aiogram.types import Message
from stfu_tg import Doc, Italic, Template

from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils.ai_mode import ModeCapabilities, resolve_chat_capabilities
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

CapabilityCheck = Callable[[ModeCapabilities], bool]


class AICapabilityFilter(Filter):
    """Passes when the chat's AI mode grants a capability.

    Defaults to "the AI is on at all". ``admins_bypass`` lets chat admins through a capability that
    is withheld from regular users, which is how moderation mode keeps /ai for admins only.
    """

    def __init__(
        self,
        check: CapabilityCheck = lambda capabilities: capabilities.ai_enabled,
        *,
        admins_bypass: bool = False,
        quiet: bool = False,
    ) -> None:
        self.check = check
        self.admins_bypass = admins_bypass
        self.quiet = quiet

    async def __call__(
        self,
        message: Message,
        chat_db: Optional[ChatModel],
        ai_capabilities: Optional[ModeCapabilities] = None,
    ) -> Union[bool, Dict[str, Any]]:
        if not chat_db:
            log.error("AICapabilityFilter: Chat not found in database, skipping")
            raise SkipHandler

        # CacheUserMessagesMiddleware resolves this once per message, with the FSM state in hand.
        capabilities = ai_capabilities or await resolve_chat_capabilities(chat_db)
        if self.check(capabilities):
            return True

        if self.admins_bypass and capabilities.ai_enabled and message.from_user:
            if await is_user_admin(chat_db.tid, message.from_user.id):
                return True

        if not capabilities.ai_enabled and not self.quiet:
            await message.reply(
                str(
                    Doc(
                        _("The AI features are currently disabled for this chat."),
                        Template(_('Please use "{cmd}" to pick an AI mode.'), cmd=Italic("/aimode")),
                    )
                )
            )

        raise SkipHandler
