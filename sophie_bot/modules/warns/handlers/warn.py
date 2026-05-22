from __future__ import annotations

from typing import Any, Optional

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder
from ass_tg.types import OptionalArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Doc, Italic, KeyValue, Section, Template, Title, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, RulesModel
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user
from sophie_bot.modules.utils_.legacy_buttons import LEGACY_RULES_BUTTON_PREFIX, build_legacy_start_payload
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.warns.utils import warn_user
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

from ..callbacks import DeleteWarnCallback


def _build_warn_reply_doc(
    target_user: ChatModel,
    admin_user_id: int,
    admin_user_name: str,
    warn_count: int,
    max_warns: int,
    reason: Optional[str],
    punishment: str | None,
) -> Doc:
    # Construct response
    doc = Doc(
        Title(_("⚠️ User warned")),
        KeyValue(_("User"), UserLink(target_user.tid, target_user.first_name_or_title)),
        KeyValue(_("By admin"), UserLink(admin_user_id, admin_user_name)),
        KeyValue(_("Warnings count"), f"{warn_count}/{max_warns}"),
    )

    if reason:
        doc += Section(Italic(reason), title=_("Reason"))

    if punishment:
        doc += Section(Template(_("User has been {punishment} due to reaching max warns."), punishment=punishment))

    return doc


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

        if message.from_user and target_user.tid == message.from_user.id:
            await message.reply(_("You cannot warn yourself."))
            return

        if await is_user_admin(connection.db_model.iid, target_user.iid):
            await message.reply(_("I cannot warn an admin."))
            return

        if not message.from_user:
            return

        # Generate AI reason if none provided and replying to a message
        if not reason and message.reply_to_message:
            replied_text = message.reply_to_message.text or message.reply_to_message.caption or None
            ai_reason = await generate_restriction_reason(
                connection.db_model,
                message_text=replied_text,
                include_rules=True,
            )
            if ai_reason:
                reason = ai_reason

        current, limit, punishment, warn = await warn_user(
            connection.db_model,
            target_user,
            admin_user,
            reason,
            trigger_message=message,
            action_context=self.data,
        )

        await log_event(
            connection.tid,
            message.from_user.id,
            LogEvent.WARN_ADDED,
            {"target_user_id": target_user.tid, "reason": reason, "current": current, "limit": limit},
        )

        doc = _build_warn_reply_doc(
            target_user,
            message.from_user.id,
            message.from_user.first_name,
            current,
            limit,
            reason,
            punishment,
        )

        # Buttons
        builder = InlineKeyboardBuilder()

        # Rules button
        if await RulesModel.get_rules(connection.db_model.iid):
            bot_username = (await self.bot.get_me()).username
            builder.row(
                InlineKeyboardButton(
                    text=f"🪧 {_('Rules')}",
                    url=f"https://t.me/{bot_username}?start={build_legacy_start_payload(LEGACY_RULES_BUTTON_PREFIX, connection.tid)}",
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

        reply_markup = builder.as_markup()
        text = doc.to_html()

        async def send_message() -> Message:
            return await self.bot.send_message(
                chat_id=message.chat.id,
                text=text,
                reply_markup=reply_markup,
                message_thread_id=message.message_thread_id,
            )

        await common_try(message.reply(text, reply_markup=reply_markup), reply_not_found=send_message)
