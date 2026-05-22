from typing import Any, Optional

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from ass_tg.types import OptionalArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import KeyValue, Section, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.admin import get_admins_rights
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.telegram_exceptions import NOT_ENOUGH_RIGHTS, RIGHT_FORBIDDEN
from sophie_bot.services.bot import bot
from sophie_bot.utils import flags
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log

PROMOTE_PERMISSIONS = (
    "can_invite_users",
    "can_change_info",
    "can_restrict_members",
    "can_delete_messages",
    "can_pin_messages",
    "can_delete_stories",
)


@flags.help(description=l_("Promotes the user to admins."))
class PromoteUserHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("promote"),
            UserRestricting(can_promote_members=True),
            BotHasPermissions(can_promote_members=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        args["admin_title"] = OptionalArg(TextArg(l_("?Admin title")))

        return args

    async def handle(self) -> Any:
        connection = self.connection

        admin_title: Optional[str] = self.data.get("admin_title")

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(_("I cannot promote myself."))

        if self.event.from_user and user.chat_id == self.event.from_user.id:
            return await self.event.reply(_("You cannot promote yourself."))

        if admin_title and len(admin_title) > 16:
            return await self.event.reply(_("Admin title is too long."))

        # Determine which permissions the invoking admin is allowed to grant.
        # Operators and chat creators may grant the full set; regular admins
        # can only delegate permissions they themselves hold.
        invoker_id = self.event.from_user.id
        grant_all = invoker_id in CONFIG.operators

        admin_record = None
        if not grant_all:
            user_model = await ChatModel.get_by_tid(invoker_id)
            chat_model = connection.db_model
            if user_model and chat_model:
                admin_record = await ChatAdminModel.find_one(
                    ChatAdminModel.chat.id == chat_model.iid,
                    ChatAdminModel.user.id == user_model.iid,
                )
                if admin_record and admin_record.member.status == ChatMemberStatus.CREATOR:
                    grant_all = True

        if grant_all:
            granted_permissions = {perm: True for perm in PROMOTE_PERMISSIONS}
        else:
            granted_permissions = {}
            if user_model and chat_model and admin_record:
                for perm in PROMOTE_PERMISSIONS:
                    granted_permissions[perm] = bool(getattr(admin_record.member, perm, False))
            else:
                # Fallback: cannot determine permissions, grant nothing extra
                granted_permissions = {perm: False for perm in PROMOTE_PERMISSIONS}

        try:
            await bot.promote_chat_member(
                chat_id=connection.tid,
                user_id=user.chat_id,
                **granted_permissions,
            )
        except TelegramBadRequest as err:
            if RIGHT_FORBIDDEN in err.message:
                log.debug("PromoteUser: Bot lacks required right, ignoring", error=str(err))
                return
            raise

        if admin_title:
            try:
                await bot.set_chat_administrator_custom_title(
                    chat_id=connection.tid, user_id=user.chat_id, custom_title=admin_title
                )
            except TelegramBadRequest as err:
                if RIGHT_FORBIDDEN in err.message or NOT_ENOUGH_RIGHTS in err.message:
                    log.debug("PromoteUser: Can't set admin title, ignoring", error=str(err))
                else:
                    raise

        # Reset admin cache
        await get_admins_rights(connection.tid, force_update=True)

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(_("Promoted by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            KeyValue(_("Admin title"), admin_title) if admin_title else _("No admin title"),
            title=_("Admin promoted successfully"),
        )

        await common_try(
            self.event.reply(str(doc)),
            reply_not_found=lambda: self.event.answer(str(doc)),
        )
