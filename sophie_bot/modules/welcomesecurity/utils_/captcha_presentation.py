from typing import NamedTuple

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from stfu_tg import Bold, Italic, Template

from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityConfirmCB, WelcomeSecurityMoveCB
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.utils.i18n import gettext as _


class CaptchaTarget(NamedTuple):
    """Keep a captcha's chat and join-request mode from the same source."""

    chat_iid: str
    is_join_request: bool


class CaptchaPresentation(NamedTuple):
    photo: BufferedInputFile
    caption: str
    reply_markup: InlineKeyboardMarkup


def build_captcha_state(captcha: EmojiCaptcha, target: CaptchaTarget) -> dict[str, object]:
    return {
        "captcha": captcha.data.model_dump(),
        "ws_chat_iid": target.chat_iid,
        "ws_is_join_request": target.is_join_request,
    }


def build_captcha_presentation(
    captcha: EmojiCaptcha,
    group_title: str,
    target: CaptchaTarget,
    *,
    incorrect: bool = False,
) -> CaptchaPresentation:
    text = Template(
        _("Complete the '{emoji_name}' emoji in order to complete the captcha and participate in the {group_name}."),
        emoji_name=Bold(captcha.data.base_emoji),
        group_name=Italic(group_title),
    )
    if incorrect:
        text += ""
        text += Bold(_("❌ Incorrect solution. Please, try again."))

    buttons = InlineKeyboardBuilder()
    buttons.row(
        InlineKeyboardButton(
            text="⬅️",
            callback_data=WelcomeSecurityMoveCB(
                direction="left", chat_iid=target.chat_iid, is_join_request=target.is_join_request
            ).pack(),
        ),
        InlineKeyboardButton(
            text="▶️",
            callback_data=WelcomeSecurityMoveCB(
                direction="right", chat_iid=target.chat_iid, is_join_request=target.is_join_request
            ).pack(),
        ),
    )
    buttons.row(
        InlineKeyboardButton(
            text=f"☑️ {_('Confirm')}",
            callback_data=WelcomeSecurityConfirmCB(
                chat_iid=target.chat_iid, is_join_request=target.is_join_request
            ).pack(),
        )
    )
    return CaptchaPresentation(
        photo=BufferedInputFile(captcha.image, "captcha.jpeg"),
        caption=str(text),
        reply_markup=buttons.as_markup(),
    )
