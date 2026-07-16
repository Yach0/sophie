from typing import Any, NamedTuple, Optional

from aiogram import Router
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from beanie import PydanticObjectId
from stfu_tg import Bold, Italic, Template

from sophie_bot.db.models import ChatModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.welcomesecurity.callbacks import (
    WelcomeSecurityConfirmCB,
    WelcomeSecurityMoveCB,
)
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptcha
from sophie_bot.utils import flags
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class CaptchaTarget(NamedTuple):
    """Which chat a captcha belongs to. Kept as a unit so the chat and its join-request
    flag can never be taken from different sources."""

    chat_iid: str
    is_join_request: bool


@flags.help(exclude=True)
class CaptchaGetHandler(SophieMessageCallbackQueryHandler):
    @classmethod
    def register(cls, router: Router):
        router.message.register(cls, CMDFilter("captcha"), IsOP(True))
        router.callback_query.register(cls, WelcomeSecurityMoveCB.filter())

    def _requested_target(self) -> Optional[CaptchaTarget]:
        """The chat this event explicitly asks a captcha for, if any."""
        if chat_iid := self.data.get("ws_chat_iid"):
            return CaptchaTarget(str(chat_iid), bool(self.data.get("ws_is_join_request", False)))

        cb_data = self.callback_data
        if isinstance(cb_data, (WelcomeSecurityMoveCB, WelcomeSecurityConfirmCB)) and cb_data.chat_iid:
            return CaptchaTarget(cb_data.chat_iid, cb_data.is_join_request)

        return None

    @staticmethod
    def _state_target(state_data: dict[str, Any]) -> Optional[CaptchaTarget]:
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
                    (
                        "The chat initiated the Welcome Security procedure were not found! "
                        "Try clicking on the authentication button in the group again."
                    )
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

        is_join_request = target.is_join_request

        cb_data = self.callback_data
        if isinstance(cb_data, WelcomeSecurityMoveCB):
            if cb_data.direction == "left":
                captcha.data.move_to_left()
            elif cb_data.direction == "right":
                captcha.data.move_to_right()
            else:
                raise SophieException("Invalid direction")

        text = Template(
            _(
                "Complete the '{emoji_name}' emoji in order to complete the captcha and participate in the {group_name}."
            ),
            emoji_name=Bold(captcha.data.base_emoji),
            group_name=Italic(chat_db.first_name_or_title),
        )

        if shuffle:
            text += ""
            text += Bold(_("❌ Incorrect solution. Please, try again."))

        buttons = InlineKeyboardBuilder()
        buttons.row(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=WelcomeSecurityMoveCB(
                    direction="left",
                    chat_iid=str(chat_db.iid),
                    is_join_request=is_join_request,
                ).pack(),
            ),
            InlineKeyboardButton(
                text="▶️",
                callback_data=WelcomeSecurityMoveCB(
                    direction="right",
                    chat_iid=str(chat_db.iid),
                    is_join_request=is_join_request,
                ).pack(),
            ),
        )
        buttons.row(
            InlineKeyboardButton(
                text=f"☑️ {_('Confirm')}",
                callback_data=WelcomeSecurityConfirmCB(
                    chat_iid=str(chat_db.iid),
                    is_join_request=is_join_request,
                ).pack(),
            )
        )

        await self.answer_media(
            BufferedInputFile(captcha.image, "captcha.jpeg"),
            caption=str(text),
            reply_markup=buttons.as_markup(),
        )

        await self.state.set_state(WelcomeSecurityFSM.captcha)
        await self.state.update_data(
            {
                "captcha": captcha.data.model_dump(),
                "ws_chat_iid": str(chat_db.iid),
                "ws_is_join_request": is_join_request,
            }
        )
