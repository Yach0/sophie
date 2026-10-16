from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from sophie_bot.db.models import ChatModel
from sophie_bot.metrics.welcome import track_captcha_sent
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.utils_.captcha_presentation import (
    CaptchaTarget,
    build_captcha_presentation,
    build_captcha_state,
)
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.utils.logger import log


class CaptchaDMBlockedError(Exception):
    """Raised when the captcha cannot be delivered because the user blocked the bot."""


async def initiate_captcha(
    user: ChatModel,
    group: ChatModel,
    is_join_request: bool = False,
    *,
    bot: Bot,
    dispatcher: Dispatcher,
) -> Message:
    """
    Generic function to initiate captcha process.

    Args:
        user: The user to send captcha to
        group: The group chat
        is_join_request: Whether this was from a join request
        bot: The bot instance used to send the captcha.
        dispatcher: The dispatcher managing the bot's FSM state.
    Returns:
        The message containing the captcha
    """
    captcha = EmojiCaptcha()
    target = CaptchaTarget(str(group.id), is_join_request)
    state: FSMContext = dispatcher.fsm.get_context(bot=bot, chat_id=user.tid, user_id=user.tid)
    await state.set_state(WelcomeSecurityFSM.captcha)
    await state.update_data(build_captcha_state(captcha, target))

    presentation = build_captcha_presentation(captcha, group.first_name_or_title, target)
    try:
        msg = await bot.send_photo(
            chat_id=user.tid,
            photo=presentation.photo,
            caption=presentation.caption,
            reply_markup=presentation.reply_markup,
        )
        track_captcha_sent()
        return msg
    except TelegramForbiddenError as err:
        log.warning(
            "initiate_captcha: could not send captcha to user DM",
            error=str(err),
            user_tid=user.tid,
            group_tid=group.tid,
            is_join_request=is_join_request,
        )
        raise CaptchaDMBlockedError from err
