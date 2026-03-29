from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import Template, UserLink

from sophie_bot.modules.troubleshooters.callbacks import CallbackActionCancel, CancelCallback
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class CancelCallbackHandler(SophieCallbackQueryHandler):
    """
    Mostly used in the wizards and other dialogs.
    Cancels the current state and deletes the message.
    The user has to be an admin to use this
    """

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (F.data == "cancel",)

    async def handle(self) -> Any:
        await self.check_for_message()

        user = self.event.from_user
        message = self.event.message
        if not isinstance(message, Message):
            return await self.event.answer(_("Message not found."))

        if not await is_user_admin(message.chat.id, user.id):
            return await self.event.answer(_("You are not allowed to cancel this action!"))

        await self.state.clear()
        await message.edit_text(_("❌ Cancelled."))


class TypedCancelCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CancelCallback.filter(),)

    async def handle(self) -> Any:
        data: CancelCallback = self.callback_data

        user = self.event.from_user

        if user.id != data.user_id:
            return await self.event.answer(_("You are not allowed to cancel this action!"))

        await self.state.clear()
        message = self.event.message
        if isinstance(message, Message):
            await message.edit_text(_("❌ Cancelled."))


class CallbackActionCancelHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CallbackActionCancel.filter(),)

    async def handle(self) -> Any:
        user = self.event.from_user
        if not user:
            return

        # Check if the user is an admin
        if not await is_user_admin(self.connection.db_model.iid, self.data["user_db"].iid):
            return await self.event.answer(_("You are not allowed to cancel this action!"))

        await self.state.clear()
        message = self.event.message
        if isinstance(message, Message):
            await message.edit_text(
                Template(_("The action was cancelled by {user}."), user=UserLink(user.id, user.first_name)).to_html()
            )
