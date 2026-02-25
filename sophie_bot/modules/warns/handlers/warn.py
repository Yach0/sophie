from __future__ import annotations

from typing import Any, Optional

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message, InlineKeyboardButton, User
from aiogram.utils.keyboard import InlineKeyboardBuilder
from ass_tg.types import TextArg, OptionalArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Doc, Title, Section, KeyValue, UserLink, Italic, Template

from sophie_bot.args.users import SophieUserArg
from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, RulesModel
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.warns.utils import warn_user
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from ..callbacks import DeleteWarnCallback


@flags.help(description=l_("Warns a user."))
@flags.disableable(name="warn")
class WarnHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("warn"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User to warn"))

        args["reason"] = OptionalArg(TextArg(l_("Reason")))

        return args

    async def handle(self) -> Any:
        message: Message = self.event
        connection = self.connection
        admin_user: ChatModel = self.data["user_db"]
        reason: Optional[str] = self.data.get("reason")

        # Get user from args or reply
        raw_user = get_arg_or_reply_user(message, self.data)

        # Get ChatModel from database (savechats middleware handles upserting)
        if isinstance(raw_user, User):
            target_user = await ChatModel.get_by_tid(raw_user.id)
            if not target_user:
                await message.reply(_("User not found in database."))
                return
        else:
            target_user = raw_user

        if target_user.tid == CONFIG.bot_id:
            await message.reply(_("I cannot warn myself."))
            return

        if await is_user_admin(connection.db_model.iid, target_user.iid):
            await message.reply(_("I cannot warn an admin."))
            return

        if not message.from_user:
            return

        # Generate AI reason if none provided
        if not reason:
            ai_reason = await generate_restriction_reason(
                connection.db_model,
                include_rules=True,
            )
            if ai_reason:
                reason = ai_reason

        current, limit, punishment, warn = await warn_user(connection.db_model, target_user, admin_user, reason)

        await log_event(
            connection.tid,
            message.from_user.id,
            LogEvent.WARN_ADDED,
            {"target_user_id": target_user.tid, "reason": reason, "current": current, "limit": limit},
        )

        # Construct response
        doc = Doc(
            Title(_("⚠️ User warned")),
            KeyValue(_("User"), UserLink(target_user.tid, target_user.first_name_or_title)),
            KeyValue(_("By admin"), UserLink(message.from_user.id, message.from_user.first_name)),
            KeyValue(_("Warnings count"), f"{current}/{limit}"),
        )

        if reason:
            doc += Section(Italic(reason), title=_("Reason"))

        if punishment:
            doc += Section(Template(_("User has been {punishment} due to reaching max warns."), punishment=punishment))

        # Buttons
        builder = InlineKeyboardBuilder()

        # Rules button
        if await RulesModel.get_rules(connection.db_model.iid):
            bot_username = (await self.bot.get_me()).username
            builder.row(
                InlineKeyboardButton(
                    text=f"🪧 {_('Rules')}",
                    url=f"https://t.me/{bot_username}?start=btn_rules_{connection.tid}",
                )
            )

        # Delete warn button
        if not punishment and warn and warn.id:
            builder.row(
                InlineKeyboardButton(
                    text=f"🗑️ {_('Delete warn')}",
                    callback_data=DeleteWarnCallback(warn_iid=str(warn.id)).pack(),
                )
            )

        await message.reply(str(doc), reply_markup=builder.as_markup())
