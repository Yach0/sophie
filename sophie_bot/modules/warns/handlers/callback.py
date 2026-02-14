from typing import Any

from aiogram.types import CallbackQuery, Message
from beanie import PydanticObjectId
from stfu_tg import Doc, Title, Section, UserLink, Template

from sophie_bot.db.models import WarnModel, ChatModel
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from ..callbacks import DeleteWarnCallback, ResetWarnsCallback, ResetAllWarnsCallback
from ..utils import delete_warn


class DeleteWarnCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters():
        return (DeleteWarnCallback.filter(),)

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        if not callback.message or not isinstance(callback.message, Message):
            return

        callback_data: DeleteWarnCallback = self.data["callback_data"]

        # Check if the user who clicked the button is an admin
        if not await is_user_admin(self.connection.db_model.iid, self.data["user_db"].iid):
            await callback.answer(_("Only admins can delete warns!"), show_alert=True)
            return

        warn_iid = PydanticObjectId(callback_data.warn_iid)
        if await delete_warn(warn_iid):
            await log_event(
                self.connection.tid,
                self.event.from_user.id,
                LogEvent.WARN_REMOVED,
                {"warn_id": str(callback_data.warn_iid)},
            )
            await callback.answer(_("Warning deleted!"))
            admin = self.data["user_db"]
            doc = Doc(
                Title(_("✅ Warning deleted")),
                Section(
                    Template(
                        _("The warning has been successfully removed by {admin}."),
                        admin=UserLink(admin.tid, admin.first_name_or_title),
                    )
                ),
            )
            await callback.message.edit_text(str(doc))
        else:
            await callback.answer(_("Warning not found or already deleted!"), show_alert=True)
            await callback.message.delete()


class ResetWarnsCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters():
        return (ResetWarnsCallback.filter(),)

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        if not callback.message or not isinstance(callback.message, Message):
            return

        callback_data: ResetWarnsCallback = self.data["callback_data"]

        # Check if the user who clicked the button is an admin
        if not await is_user_admin(self.connection.db_model.iid, self.data["user_db"].iid):
            await callback.answer(_("Only admins can reset warns!"), show_alert=True)
            return

        chat_iid = self.connection.db_model.iid
        target_user_tid = callback_data.user_tid

        target_user = await ChatModel.get_by_tid(target_user_tid)
        if not target_user:
            await callback.answer(_("User not found!"), show_alert=True)
            return

        await WarnModel.find(WarnModel.chat.id == chat_iid, WarnModel.user.id == target_user.iid).delete()

        await log_event(
            self.connection.tid,
            self.event.from_user.id,
            LogEvent.WARN_RESET,
            {"target_user_id": target_user_tid},
        )

        target_user = await ChatModel.get_by_tid(target_user_tid)
        target_user_name = target_user.first_name_or_title if target_user else _("User")

        await callback.answer(_("Warnings reset!"))
        return await callback.message.edit_text(
            str(Template(_("Reset warnings of {user}."), user=UserLink(target_user_tid, target_user_name)))
        )


class ResetAllWarnsCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters():
        return (ResetAllWarnsCallback.filter(),)

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        if not callback.message or not isinstance(callback.message, Message):
            return

        # Check if the user who clicked the button is an admin
        if not await is_user_admin(self.connection.db_model.iid, self.data["user_db"].iid):
            await callback.answer(_("Only admins can reset all warns!"), show_alert=True)
            return

        chat_iid = self.connection.db_model.iid
        await WarnModel.find(WarnModel.chat.id == chat_iid).delete()
        await log_event(self.connection.tid, self.event.from_user.id, LogEvent.ALL_WARNS_RESET)

        await callback.answer(_("All warnings reset!"))
        return await callback.message.edit_text(_("Reset all warnings in this chat."))
