import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, Message, TelegramObject, User
from aiogram.utils.keyboard import InlineKeyboardBuilder
from stfu_tg import Doc

from sophie_bot.config import CONFIG
from sophie_bot.constants import WELCOMESECURITY_JOIN_TIMEOUT_MINUTES
from sophie_bot.db.models import ChatModel, GreetingsModel, RulesModel
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes import Saveable
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.greetings.default_welcome import (
    get_default_security_message,
    get_default_welcome_message,
)
from sophie_bot.modules.greetings.utils.send_welcome import send_welcome
from sophie_bot.modules.notes.utils.buttons.renderer import render_buttons
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.telegram_exceptions import REPLY_MESSAGE_INVALID
from sophie_bot.modules.welcomesecurity.utils_.on_new_user import ws_on_new_users_mute
from sophie_bot.modules.welcomesecurity.utils_.welcomemute import on_welcomemute
from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


class NewUserMiddleware(BaseMiddleware):
    @staticmethod
    def build_welcomesecurity_keyboard(chat_tid: int) -> InlineKeyboardBuilder:
        buttons = InlineKeyboardBuilder()
        rendered_button = render_buttons(
            [[Button(text=_("I am not a robot"), action=ButtonAction.captcha)]], chat_tid
        ).inline_keyboard
        for row in rendered_button:
            buttons.row(*row)
        return buttons

    @staticmethod
    def is_join_too_old(message: Message) -> bool:
        """Check if the join message is older than the timeout threshold."""
        if not message.date:
            return False
        time_diff = datetime.now(UTC) - message.date
        return time_diff > timedelta(minutes=WELCOMESECURITY_JOIN_TIMEOUT_MINUTES)

    @staticmethod
    async def cleanup(db_item: GreetingsModel, message: Message, sent_message: Message | None) -> GreetingsModel:
        to_delete: list[int] = []

        # Clean service
        if db_item.clean_service and db_item.clean_service.enabled:
            to_delete.append(message.message_id)

        # Clean welcome
        if db_item.clean_welcome and db_item.clean_welcome.enabled:
            if db_item.clean_welcome.last_msg:
                to_delete.append(db_item.clean_welcome.last_msg)

            # Save the new one
            if sent_message:
                db_item = await db_item.clean_welcome_new_message(sent_message.message_id)

        # TODO: Handle exceptions
        if to_delete:
            await common_try(bot.delete_messages(chat_id=message.chat.id, message_ids=to_delete))

        # Save the new one
        return db_item

    @staticmethod
    async def self_welcome(message: Message):
        doc = Doc(
            _("Hi, Thank you for choosing Sophie for your group!"),
            _("Please read the documentation to learn more about Sophie and do not hesitate to join the Support Chat"),
        )

        buttons = InlineKeyboardBuilder()
        buttons.add(
            InlineKeyboardButton(text=_("Documentation"), url=CONFIG.wiki_link),
            InlineKeyboardButton(text=_("Support Chat"), url=CONFIG.support_link),
        )
        markup = buttons.as_markup()

        try:
            return await message.reply(str(doc), reply_markup=markup)
        except TelegramBadRequest as err:
            if REPLY_MESSAGE_INVALID in err.message:
                log.debug("NewUserMiddleware: Reply message invalid on self_welcome, falling back to answer")
                return await message.answer(str(doc), reply_markup=markup)
            raise

    @staticmethod
    async def is_join_request(chat_db: ChatModel, user_db: ChatModel) -> bool:
        key = f"chat_ws_join_request:{chat_db.iid}:{user_db.iid}"
        join_request = await aredis.get(key)
        if join_request:
            await aredis.delete(key)
        return bool(join_request)

    @staticmethod
    async def on_captcha(
        message: Message,
        db_item: GreetingsModel,
        chat_db: ChatModel,
        new_users: list[ChatModel],
        new_member: User,
        cleanservice_enabled: bool,
        chat_rules: RulesModel | None,
    ) -> Message | None:
        muted_users = await ws_on_new_users_mute(new_users, chat_db)

        # If no users were welcomesecurity muted, fall back to the normal welcome flow.
        if not any(muted_users):
            return None

        ws_saveable: Saveable = db_item.security_note or get_default_security_message()
        security_keyboard = NewUserMiddleware.build_welcomesecurity_keyboard(chat_db.tid)

        async def send_to(user: ChatModel | None) -> Message | None:
            return await send_welcome(
                message,
                ws_saveable,
                cleanservice_enabled,
                chat_rules,
                user=new_member,
                additional_keyboard=security_keyboard.as_markup(),
                owner_chat_tid=chat_db.tid,
                receiver_user_id=user.tid if user else None,
            )

        if await is_enabled("welcomecaptcha_ephemeral", chat_tid=chat_db.tid):
            # One prompt per new member, visible only to them. Nothing is left in the chat, so
            # nothing is recorded for the cleanup that deletes the prompt once the captcha passes.
            sent = [await send_to(user) for user, muted in zip(new_users, muted_users) if muted]
            return next((message for message in sent if message), None)

        sent_message = await send_to(None)
        # Save sent message to cleanup it later
        if sent_message and len(muted_users) == 1:
            await aredis.set(f"chat_ws_message:{chat_db.iid}:{new_users[0].iid}", sent_message.message_id)

        return sent_message

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # TODO: Handle multiple users add

        if isinstance(event, Message) and event.new_chat_members:
            if not event.from_user:
                raise ValueError("NewUserMiddleware: 'event.from_user' is None!")

            adder_id = event.from_user.id
            chat_id: int = event.chat.id
            chat_db: ChatModel = data["chat_db"]
            new_users: list[ChatModel] = data["new_users"]

            # Bot was added to the chat
            if any(user for user in event.new_chat_members if user.id == CONFIG.bot_id):
                await self.self_welcome(event)
                return await handler(event, data)

            db_item: GreetingsModel = await GreetingsModel.get_by_chat_iid(chat_db.iid)

            # Check if any of the new users was from a join request
            # Join request users already got their greeting from the captcha flow, but their
            # service message still has to be cleaned up like any other join.
            is_from_join_request = False
            for user in new_users:
                if await self.is_join_request(chat_db, user):
                    is_from_join_request = True
                    break

            if is_from_join_request:
                await self.cleanup(db_item, event, None)
                return await handler(event, data)

            # Sanity check
            if tuple(user.id for user in event.new_chat_members) != tuple(user.tid for user in new_users):
                raise ValueError("NewUserMiddleware: unexpected / incorrect 'new_users' data from SaveChatsMiddleware!")

            human_users = [new_user for new_user in new_users if not new_user.is_bot]

            # The greeting is about whoever joined, not about whoever produced the service message.
            new_member = event.new_chat_members[0]

            cleanservice_enabled = bool(db_item.clean_service and db_item.clean_service.enabled)

            is_adder_admin = await is_user_admin(chat_db.iid, adder_id)

            sent_message: Message | None = None

            chat_rules = await RulesModel.get_rules(chat_db.iid)
            welcomecaptcha_enabled = await is_enabled("welcomecaptcha", chat_tid=chat_db.tid)

            # The origin user of the message is admin could indite:
            # 1. Chat owner joined the chat back
            # 2. One of admins added user/users, we do not want to enforce welcomesecurity
            # 3. Join message is too old (bot was down/lagging), skip captcha enforcement
            join_is_too_old = self.is_join_too_old(event)
            if not (
                db_item.welcome_disabled
                or (db_item.welcome_security and db_item.welcome_security.enabled and welcomecaptcha_enabled)
            ) or (not db_item.welcome_disabled and is_adder_admin):
                welcome_saveable: Saveable = db_item.note or get_default_welcome_message(bool(chat_rules))
                if await is_enabled("greetings_ephemeral", chat_tid=chat_db.tid):
                    # One greeting per member, visible only to them and filled with their own name.
                    # None of them is in the chat, so none is handed to the clean-welcome cleanup.
                    for member in event.new_chat_members:
                        if member.is_bot:
                            continue
                        await send_welcome(
                            event,
                            welcome_saveable,
                            cleanservice_enabled,
                            chat_rules,
                            user=member,
                            owner_chat_tid=chat_db.tid,
                            receiver_user_id=member.id,
                        )
                else:
                    sent_message = await send_welcome(
                        event,
                        welcome_saveable,
                        cleanservice_enabled,
                        chat_rules,
                        user=new_member,
                        owner_chat_tid=chat_db.tid,
                    )

                if db_item.welcome_mute and db_item.welcome_mute.enabled and db_item.welcome_mute.time:
                    welcome_mute_time = db_item.welcome_mute.time
                    await asyncio.gather(
                        *(on_welcomemute(chat_id, new_user.tid, welcome_mute_time) for new_user in human_users)
                    )

            elif (
                not is_adder_admin
                and db_item.welcome_security
                and db_item.welcome_security.enabled
                and welcomecaptcha_enabled
                and not join_is_too_old
            ):
                # If group has join_by_request enabled, captcha is handled by join request handler
                # Otherwise, use normal captcha
                if human_users and not event.chat.join_by_request:
                    sent_message = await self.on_captcha(
                        event, db_item, chat_db, human_users, new_member, cleanservice_enabled, chat_rules
                    )

            # Cleanup
            await self.cleanup(db_item, event, sent_message)

            # Skip handler
            raise SkipHandler

        return await handler(event, data)
