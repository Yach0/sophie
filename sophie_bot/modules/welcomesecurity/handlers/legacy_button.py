from re import search
from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

from sophie_bot.config import CONFIG
from sophie_bot.db.models import (
    ChatModel,
    GreetingsModel,
    UserInGroupModel,
    WSUserModel,
)
from sophie_bot.modules.federations.services import FederationBanService, FederationManageService
from sophie_bot.modules.restrictions.utils.restrictions import unmute_user
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_WELCOME_SECURITY_BUTTON_PATTERN,
    LEGACY_WELCOME_SECURITY_BUTTON_PREFIX,
    LEGACY_WELCOME_SECURITY_STABLE_PREFIX,
)
from sophie_bot.modules.welcomesecurity.handlers.captcha_get import CaptchaGetHandler
from sophie_bot.services.bot import bot
from sophie_bot.utils.handlers import (
    SophieCallbackQueryHandler,
    SophieMessageHandler,
)
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


class LegacyStableWSButtonRedirectHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (F.data.startswith(LEGACY_WELCOME_SECURITY_STABLE_PREFIX),)

    async def handle(self) -> Any:
        chat_id = self.event.message.chat.id
        return await self.event.answer(
            url=f"https://t.me/{CONFIG.username}?start={LEGACY_WELCOME_SECURITY_BUTTON_PREFIX}_{chat_id}"
        )


class LegacyWSButtonHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (F.text.regexp(rf"/start {LEGACY_WELCOME_SECURITY_BUTTON_PREFIX}_(.*)"),)

    @staticmethod
    async def _user_is_still_in_group(user_db: ChatModel, group_db: ChatModel) -> bool:
        if await UserInGroupModel.get_user_in_group(user_db.iid, group_db.iid):
            return True

        try:
            member = await bot.get_chat_member(chat_id=group_db.tid, user_id=user_db.tid)
        except TelegramBadRequest as err:
            log.warning(
                "LegacyWSButtonHandler: failed to validate membership via Telegram",
                user=user_db.iid,
                group=group_db.iid,
                error=str(err),
            )
            return False

        if member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
            return False

        await UserInGroupModel.ensure_user_in_group(user_db, group_db)
        return True

    async def handle(self) -> Any:
        if not self.event.text:
            return

        match = search(LEGACY_WELCOME_SECURITY_BUTTON_PATTERN, self.event.text)
        if not match:
            return

        chat_id = int(match.group(1))

        if not (group_db := await ChatModel.get_by_tid(chat_id)):
            log.warning("LegacyWSButtonHandler: Group not found in DB", chat_id=chat_id)
            return await self.event.reply(_("This group no longer exists or has been removed."))

        log.debug("LegacyWSButtonHandler: Handling WS button press", group=group_db.iid, chat_id=chat_id)

        user_db: ChatModel = self.data["user_db"]

        ws_user = await WSUserModel.is_user(user_db.iid, group_db.iid)
        if not ws_user:
            log.warning("LegacyWSButtonHandler: WSUserModel not found", user=user_db.iid, group=group_db.iid)
            return await self.event.reply(
                _("It seems like you do not have to pass the welcome security authentication")
            )

        if not ws_user.is_join_request and not await self._user_is_still_in_group(user_db, group_db):
            log.warning("LegacyWSButtonHandler: UserInGroupModel not found", user=user_db.iid, group=group_db.iid)
            return await self.event.reply(
                _("It seems like you are not belong to the chat anymore. Are you sure you joined the group?")
            )

        if await is_user_admin(chat_id, user_db.iid):
            # Only drop the pending WS record once the unmute succeeds; otherwise the admin
            # would be left muted with no record to re-enter this flow and retry.
            if await unmute_user(chat_tid=chat_id, user_tid=user_db.tid):
                await WSUserModel.remove_user(user_db.iid, group_db.iid)
            log.debug("LegacyWSButtonHandler: User is admin, no need to pass WS", user=user_db.iid, group=group_db.iid)
            return await self.event.reply(
                _("You already an admin in the chat, therefore you don't need to pass the authentication!")
            )

        # Check if banned
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_db.tid)
            if member.status == ChatMemberStatus.KICKED:
                return await self.event.reply(_("You are banned in this chat, so you cannot pass the authentication!"))
        except TelegramBadRequest:
            pass

        # Check fedban
        federation = await FederationManageService.get_federation_for_chat(group_db.iid)
        if federation:
            ban_info = await FederationBanService.is_user_banned_in_chain(federation.fed_id, user_db.tid)
            if ban_info:
                return await self.event.reply(
                    _("You are banned in the federation, so you cannot pass the authentication!")
                )

        ws_db_item = await GreetingsModel.get_by_chat_iid(group_db.iid)

        if not ws_db_item.welcome_security or not ws_db_item.welcome_security.enabled:
            # We still allow users to complete it, because it could've been disabled afterwards
            log.warning("LegacyWSButtonHandler: WS is disabled but we still allow users to complete")

        # Initialize captcha
        self.data["ws_chat_iid"] = group_db.iid
        self.data["ws_is_join_request"] = ws_user.is_join_request
        return await CaptchaGetHandler(self.event, **self.data).handle()
