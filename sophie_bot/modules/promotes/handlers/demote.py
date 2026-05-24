from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import KeyValue, Section, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.config import CONFIG
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.admin import get_admins_rights
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.modules.utils_.telegram_exceptions import BOTS_CANT_ADD_NEW_CHAT_MEMBERS
from sophie_bot.services.bot import bot
from sophie_bot.utils import flags
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log


@flags.help(description=l_("Demotes the user from admins."))
class DemoteUserHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("demote"),
            UserRestricting(can_promote_members=True),
            BotHasPermissions(can_promote_members=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"user": SophieUserArg(l_("User"))} if not message or not is_real_reply(message) else {}

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(_("I cannot demote myself."))

        if self.event.from_user and user.chat_id == self.event.from_user.id:
            return await self.event.reply(_("You cannot demote yourself."))

        try:
            await bot.promote_chat_member(
                chat_id=connection.tid,
                user_id=user.chat_id,
                can_invite_users=False,
                can_change_info=False,
                can_restrict_members=False,
                can_delete_messages=False,
                can_pin_messages=False,
                can_delete_stories=False,
            )
        except TelegramBadRequest as err:
            if BOTS_CANT_ADD_NEW_CHAT_MEMBERS in err.message:
                log.debug("DemoteUser: Bot can't manage chat members, ignoring", error=str(err))
                return
            raise

        # Reset admin cache
        await get_admins_rights(connection.tid, force_update=True)

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(_("Demoted by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            title=_("User demoted successfully"),
        )

        await reply_or_answer(self.event, doc)
