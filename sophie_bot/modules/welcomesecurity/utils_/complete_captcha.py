from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message
from redis.asyncio import Redis

from sophie_bot.db.models import ChatModel, GreetingsModel, WSUserModel
from sophie_bot.db.models.greetings import WelcomeMute
from sophie_bot.metrics.welcome import track_captcha_passed
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.telegram_exceptions import MSG_NOT_MODIFIED, USER_ALREADY_PARTICIPANT
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.modules.welcomesecurity.utils_.on_user_passed import ws_on_user_passed
from sophie_bot.utils.i18n import gettext as _


async def complete_captcha(
    user: ChatModel,
    group: ChatModel,
    greetings: GreetingsModel,
    captcha_message: Message,
    is_join_request: bool = False,
    *,
    bot: Bot,
    redis: Redis,
) -> None:
    """
    Generic function to complete captcha process.

    The captcha flow already shows rules in DM (via captcha_send_rules) and the security
    note in the group. No welcome or rules messages are sent to the group after captcha
    completion — welcome messages are only sent to the group when captcha is disabled
    (handled by NewUserMiddleware).

    Args:
        user: The user who completed captcha
        group: The group chat
        greetings: Greetings model
        captcha_message: The message containing the captcha
        is_join_request: Whether this was from a join request
        bot: Telegram bot used to update the captcha and membership.
        redis: Redis connection used by the welcome-security flow.
    """
    pending = await WSUserModel.is_user(user.iid, group.iid)
    if pending is None or pending.passed:
        return
    if pending.transition is None:
        pending = await pending.claim_transition("completing")
    if pending is None or pending.transition != "completing" or not await pending.transition_is_current():
        return
    is_join_request = pending.is_join_request

    # Mark captcha as correct
    track_captcha_passed()
    captcha = EmojiCaptcha()
    captcha.show_emoji("✅")

    # Update the captcha message
    try:
        await bot.edit_message_media(
            media=InputMediaPhoto(
                media=BufferedInputFile(captcha.image, "captcha.jpeg"),
                caption=_("You're all set, and can now participate in the conversation"),
            ),
            chat_id=captcha_message.chat.id,
            message_id=captcha_message.message_id,
        )
    except TelegramBadRequest as error:
        if MSG_NOT_MODIFIED not in error.message:
            raise

    # Approve join request if applicable
    if is_join_request:
        if not await pending.transition_is_current():
            return
        await redis.set(f"chat_ws_join_request:{group.iid}:{user.iid}", 1, ex=172800)
        if not await pending.transition_is_current():
            return
        try:
            await bot.approve_chat_join_request(chat_id=group.tid, user_id=user.tid)
        except TelegramBadRequest as error:
            if USER_ALREADY_PARTICIPANT not in error.message:
                raise

    # Unmute user from welcomesecurity (and apply welcome_mute if enabled)
    restriction_succeeded = await ws_on_user_passed(
        user,
        group,
        greetings.welcome_mute or WelcomeMute(),
        bot=bot,
        redis=redis,
        pending=pending,
    )

    if not restriction_succeeded:
        return

    # Clean up the security note message from the group
    if msg_to_clean := await redis.get(f"chat_ws_message:{group.iid}:{user.iid}"):
        await common_try(bot.delete_message(chat_id=group.tid, message_id=int(msg_to_clean)))
        await redis.delete(f"chat_ws_message:{group.iid}:{user.iid}")

    if is_join_request and (msg_id := await redis.get(f"join_request_message:{group.iid}:{user.iid}")):
        await common_try(bot.delete_message(chat_id=group.tid, message_id=int(msg_id)))
