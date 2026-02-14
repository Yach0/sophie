from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import ChatJoinRequest

from sophie_bot.constants import WELCOMESECURITY_JOIN_TIMEOUT_MINUTES
from sophie_bot.db.models import ChatModel, GreetingsModel, RulesModel
from sophie_bot.modules.greetings.default_welcome import get_default_join_request_message
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.utils_.telegram_exceptions import (
    CHANNELS_TOO_MUCH,
    CHAT_ADMIN_REQUIRED,
    USER_CHANNELS_TOO_MUCH,
    HIDE_REQUESTER_MISSING,
)
from sophie_bot.utils.handlers import SophieBaseHandler
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import initiate_captcha
from sophie_bot.modules.welcomesecurity.utils_.on_new_user import ws_on_new_user
from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


class ChatJoinRequestHandler(SophieBaseHandler[ChatJoinRequest]):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return ()

    @classmethod
    def register(cls, router):
        router.chat_join_request.register(cls, *cls.filters())

    async def handle(self) -> Any:
        chat_tid = self.event.chat.id
        user_tid = self.event.from_user.id
        connection = self.connection

        async def _approve_request() -> None:
            try:
                await common_try(self.event.approve())
            except TelegramBadRequest as err:
                log.warning("Could not approve join request", err=err)

                if CHANNELS_TOO_MUCH in err.message or USER_CHANNELS_TOO_MUCH in err.message:
                    await self.event.decline()
                    return None
                elif CHAT_ADMIN_REQUIRED in err.message:
                    return None
                elif HIDE_REQUESTER_MISSING in err.message:
                    return None
                else:
                    raise err

        # Check if user is admin
        if await is_user_admin(chat_tid, user_tid):
            # Approve immediately
            await _approve_request()
            return

        # Get chat model
        chat = await ChatModel.get_by_tid(chat_tid)
        if not chat:
            return

        # Get greetings model
        greetings = await GreetingsModel.get_by_chat_iid(chat.iid)

        # Check if join request is too old (bot was down/lagging)
        # If too old, skip captcha enforcement and approve immediately
        if self.event.date:
            time_diff = datetime.now(timezone.utc) - self.event.date
            if time_diff > timedelta(minutes=WELCOMESECURITY_JOIN_TIMEOUT_MINUTES):
                await _approve_request()
                return

        # Check if welcomesecurity is enabled
        if not (greetings.welcome_security and greetings.welcome_security.enabled):
            # If welcome security is not enabled, don't auto-approve
            # Let admins handle the approval manually
            return

        # Get user model
        user = await ChatModel.get_by_tid(user_tid)
        if not user:
            return

        # Mute the user (similar to ws_on_new_user)
        muted = await ws_on_new_user(user, chat, is_join_request=True)
        if not muted:
            await _approve_request()
            return

        # Send join request message in chat
        join_request_saveable = greetings.join_request_message or get_default_join_request_message()

        rules = await RulesModel.get_rules(connection.db_model.iid)
        additional_fillings = {"rules": rules.text or "" if rules else _("No chat rules, have fun!")}

        # Send message in chat
        sent_message = await send_saveable(
            None, chat_tid, join_request_saveable, additional_fillings=additional_fillings, user=self.event.from_user
        )

        # Store message ID for cleanup
        await aredis.set(f"join_request_message:{chat_tid}:{user_tid}", sent_message.message_id, ex=172800)

        # Mark this user as having an active join request to prevent re-processing in new_user middleware
        await aredis.set(f"chat_ws_message:{chat.iid}:{user.iid}", sent_message.message_id, ex=172800)

        # Apply clean_welcome to the join request message
        if greetings.clean_welcome and greetings.clean_welcome.enabled:
            if greetings.clean_welcome.last_msg:
                await common_try(bot.delete_message(chat_id=chat_tid, message_id=greetings.clean_welcome.last_msg))

        # Send captcha to user's DM
        await initiate_captcha(user, chat, is_join_request=True)
