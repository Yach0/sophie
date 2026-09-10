from typing import Any

from ass_tg.types import IntArg
from stfu_tg import Template

from sophie_bot.db.models import BetaModeModel, GlobalSettings
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


@flags.args(
    percentage=IntArg(),
)
class SetBetaPercentage(SophieMessageHandler):
    @staticmethod
    def filters():
        return (CMDFilter("op_setbeta"), IsOP(True))

    async def handle(self) -> Any:
        percentage: int = self.data["percentage"]

        if percentage < 0 or percentage > 100:
            return await self.event.reply(_("Please enter a number between 0 and 100."))

        model = await GlobalSettings.set_by_key("beta_percentage", percentage)
        return await self.event.reply(
            Template(
                _("The beta percentage has been set to {percentage}% for all new chats."),
                percentage=model.value,
            ).to_html()
        )


class ResetBetaChats(SophieMessageHandler):
    @staticmethod
    def filters():
        return (CMDFilter("op_resetbeta"), IsOP(True))

    async def handle(self) -> Any:
        await BetaModeModel.all_chats_reset_current_mode()
        return await self.event.reply(_("The chosen beta mode has been reset for all chats."))
