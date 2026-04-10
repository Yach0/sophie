from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from stfu_tg import Bold, Italic, Template

from sophie_bot.db.models import ChatModel
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityMoveCB, WelcomeSecurityConfirmCB
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.services.bot import bot, dp
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


class CaptchaDMBlockedError(Exception):
    """Raised when the captcha cannot be delivered because the user blocked the bot."""


async def _prepare_captcha_state(user_tid: int, group: ChatModel, captcha: EmojiCaptcha, is_join_request: bool) -> None:
    state: FSMContext = dp.fsm.get_context(bot=bot, chat_id=user_tid, user_id=user_tid)
    await state.set_state(WelcomeSecurityFSM.captcha)
    await state.update_data(
        captcha=captcha.data.model_dump(),
        ws_chat_iid=str(group.iid),
        ws_is_join_request=is_join_request,
    )


async def initiate_captcha(
    user: ChatModel,
    group: ChatModel,
    is_join_request: bool = False,
) -> Message:
    """
    Generic function to initiate captcha process.

    Args:
        user: The user to send captcha to
        group: The group chat
        :param is_join_request: Whether this was from a join request
    Returns:
        The message containing the captcha
    """
    # Generate captcha
    captcha = EmojiCaptcha()
    await _prepare_captcha_state(user.tid, group, captcha, is_join_request)

    # Create text
    text = Template(
        _("Complete the '{emoji_name}' emoji in order to complete the captcha and participate in the {group_name}."),
        emoji_name=Bold(captcha.data.base_emoji),
        group_name=Italic(group.first_name_or_title),
    )

    # Create buttons
    buttons = InlineKeyboardBuilder()
    buttons.row(
        InlineKeyboardButton(
            text="⬅️",
            callback_data=WelcomeSecurityMoveCB(
                direction="left", chat_iid=str(group.iid), is_join_request=is_join_request
            ).pack(),
        ),
        InlineKeyboardButton(
            text="▶️",
            callback_data=WelcomeSecurityMoveCB(
                direction="right", chat_iid=str(group.iid), is_join_request=is_join_request
            ).pack(),
        ),
    )
    buttons.row(
        InlineKeyboardButton(
            text=f"☑️ {_('Confirm')}",
            callback_data=WelcomeSecurityConfirmCB(chat_iid=str(group.iid), is_join_request=is_join_request).pack(),
        )
    )

    # DM mode: send to user's DM
    try:
        return await bot.send_photo(
            chat_id=user.tid,
            photo=BufferedInputFile(captcha.image, "captcha.jpeg"),
            caption=str(text),
            reply_markup=buttons.as_markup(),
        )
    except TelegramForbiddenError as err:
        log.warning(
            "initiate_captcha: could not send captcha to user DM",
            error=str(err),
            user_tid=user.tid,
            group_tid=group.tid,
            is_join_request=is_join_request,
        )
        raise CaptchaDMBlockedError from err
