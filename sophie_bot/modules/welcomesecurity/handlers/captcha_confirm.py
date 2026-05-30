from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters import or_f
from aiogram.types import Message
from beanie import PydanticObjectId

from sophie_bot.db.models import ChatModel, GreetingsModel, RulesModel
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.modules.welcomesecurity.callbacks import (
    WelcomeSecurityConfirmCB,
    WelcomeSecurityRulesAgreeCB,
)
from sophie_bot.modules.welcomesecurity.handlers.captcha_get import CaptchaGetHandler
from sophie_bot.modules.welcomesecurity.utils_.complete_captcha import complete_captcha
from sophie_bot.modules.welcomesecurity.utils_.captcha_rules import captcha_send_rules
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


class CaptchaConfirmHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (or_f(WelcomeSecurityConfirmCB().filter(), WelcomeSecurityRulesAgreeCB().filter()),)

    async def captcha_correct(self, group: ChatModel, state_data: dict[str, Any]):
        if not isinstance(self.event.message, Message):
            raise SophieException("Invalid message type. Try initializing the captcha again.")

        user = self.data["user_db"]
        is_join_request = self.callback_data.is_join_request if self.callback_data else False

        if not isinstance(self.data["callback_data"], WelcomeSecurityRulesAgreeCB) and (
            rules := await RulesModel.get_rules(group.iid)
        ):
            return await captcha_send_rules(self.event.message, rules, group.iid, is_join_request)

        await self.state.clear()

        greetings_db = await GreetingsModel.get_by_chat_iid(group.iid)

        return await complete_captcha(user, group, greetings_db, self.event.message, is_join_request)

    async def handle(self) -> Any:
        data = await self.state.get_data()

        # Try to get chat_iid from state, then from callback_data
        chat_iid = data.get("ws_chat_iid")
        if not chat_iid and hasattr(self, "callback_data") and self.callback_data:
            if hasattr(self.callback_data, "chat_iid") and self.callback_data.chat_iid:
                chat_iid = self.callback_data.chat_iid

        if not chat_iid:
            await self.event.answer(_("Captcha expired. Please try again."))
            return

        chat_db = await ChatModel.get_by_iid(PydanticObjectId(chat_iid))
        if not chat_db:
            raise ValueError("No chat found in database")

        if "captcha" not in data:
            log.warning("Captcha callback with no captcha data in FSM state, likely expired")
            await self.event.answer(_("Captcha expired. Please try again."))
            return

        captcha = EmojiCaptcha(data=data["captcha"])

        if captcha.data.is_correct:
            return await self.captcha_correct(chat_db, data)
        self.data["ws_shuffle"] = True
        return await CaptchaGetHandler(self.event, **self.data)
