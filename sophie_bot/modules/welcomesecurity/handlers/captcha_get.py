from typing import Any

from aiogram import Router
from beanie import PydanticObjectId

from sophie_bot.db.models import ChatModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.welcomesecurity.callbacks import (
    WelcomeSecurityConfirmCB,
    WelcomeSecurityMoveCB,
)
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.utils_.captcha_presentation import (
    CaptchaTarget,
    build_captcha_presentation,
    build_captcha_state,
)
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.utils import flags
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


@flags.handler_help(exclude=True)
class CaptchaGetHandler(SophieMessageCallbackQueryHandler):
    @classmethod
    def register(cls, router: Router) -> None:
        router.message.register(cls, CMDFilter("captcha"), IsOP(True))
        router.callback_query.register(cls, WelcomeSecurityMoveCB.filter())

    def _requested_target(self) -> CaptchaTarget | None:
        """The chat this event explicitly asks a captcha for, if any."""
        if chat_iid := self.data.get("ws_chat_iid"):
            return CaptchaTarget(str(chat_iid), bool(self.data.get("ws_is_join_request", False)))

        cb_data = self.callback_data
        if isinstance(cb_data, (WelcomeSecurityMoveCB, WelcomeSecurityConfirmCB)) and cb_data.chat_iid:
            return CaptchaTarget(cb_data.chat_iid, cb_data.is_join_request)

        return None

    @staticmethod
    def _state_target(state_data: dict[str, Any]) -> CaptchaTarget | None:
        """The chat of the captcha currently in progress, if any."""
        if chat_iid := state_data.get("ws_chat_iid"):
            return CaptchaTarget(str(chat_iid), bool(state_data.get("ws_is_join_request", False)))

        return None

    async def handle(self) -> Any:
        state_data = await self.state.get_data()

        state_target = self._state_target(state_data)
        target = self._requested_target() or state_target

        if not target:
            await self.answer(
                _(
                    "The chat initiated the Welcome Security procedure were not found! "
                    "Try clicking on the authentication button in the group again."
                )
            )
            await self.state.clear()
            return

        # An abandoned captcha for another chat must not be served for the requested one.
        if state_target and state_target.chat_iid != target.chat_iid:
            state_data = {}

        chat_db = await ChatModel.get_by_iid(PydanticObjectId(target.chat_iid))
        if not chat_db:
            await self.answer(_("Chat not found in database"))
            await self.state.clear()
            return

        shuffle: bool = self.data.get("ws_shuffle", False)

        # Restore from state or generate new
        captcha = EmojiCaptcha(data=state_data.get("captcha") if not shuffle else None)

        cb_data = self.callback_data
        if isinstance(cb_data, WelcomeSecurityMoveCB):
            if cb_data.direction == "left":
                captcha.data.move_to_left()
            elif cb_data.direction == "right":
                captcha.data.move_to_right()
            else:
                raise SophieException("Invalid direction")

        presentation = build_captcha_presentation(captcha, chat_db.first_name_or_title, target, incorrect=shuffle)
        await self.answer_media(
            presentation.photo,
            caption=presentation.caption,
            reply_markup=presentation.reply_markup,
        )

        await self.state.set_state(WelcomeSecurityFSM.captcha)
        await self.state.update_data(build_captcha_state(captcha, target))
