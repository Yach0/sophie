from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.db.models.clean_notes import CleanNotesModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.utils_.status_handler import StatusBoolHandlerABC
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Shows / changes the state of automatic notes cleanup."))
class CleanNotesHandlerABC(StatusBoolHandlerABC):
    header_text = l_("Automatic notes cleanup")
    change_command = "cleannotes"

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("cleannotes"), FeatureFlagFilter("cleannotes"), UserRestricting(admin=True)

    async def get_status(self) -> bool:
        chat_iid = self.connection.db_model.iid
        db_model = await CleanNotesModel.get_by_chat_iid(chat_iid)
        return db_model.enabled

    async def set_status(self, new_status: bool) -> None:
        chat_iid = self.connection.db_model.iid

        db_model = await CleanNotesModel.get_by_chat_iid(chat_iid)
        await db_model.set_status(new_status)
