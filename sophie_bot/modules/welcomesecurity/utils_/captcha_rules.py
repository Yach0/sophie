from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from beanie import PydanticObjectId
from stfu_tg import Doc, Title

from sophie_bot.db.models import RulesModel
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityRulesAgreeCB
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.modules.welcomesecurity.utils_.send_captcha import send_captcha_message
from sophie_bot.utils.i18n import gettext as _


async def captcha_send_rules(message: Message, rules: RulesModel, chat_iid: PydanticObjectId, is_join_request: bool):
    captcha = EmojiCaptcha()
    captcha.show_emoji("🪧")

    title = Title(_("Please read the chat rules"))
    doc = Doc(title, rules.text)

    buttons = InlineKeyboardBuilder()
    buttons.add(
        InlineKeyboardButton(
            text=f"✅ {_('I agree')}",
            callback_data=WelcomeSecurityRulesAgreeCB(
                chat_iid=str(chat_iid),
                is_join_request=is_join_request,
            ).pack(),
        )
    )

    if len(str(doc)) >= 1024 or rules.file:
        # Captions can't be longer than 1024, send a normal message text instead this time
        # Or a file
        return await send_saveable(
            message, message.chat.id, rules, title=title, additional_keyboard=buttons.as_markup()
        )

    return await send_captcha_message(message, captcha, str(doc), reply_markup=buttons.as_markup())
