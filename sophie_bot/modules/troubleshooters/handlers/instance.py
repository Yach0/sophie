from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import KeyValue, Section

from sophie_bot.db.models import GlobalSettings
from sophie_bot.db.models.beta import BetaModeModel, CurrentMode, PreferredMode
from sophie_bot.db.models.chat import ChatType
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Show the instance and instance preference for this chat"))
class InstanceStatus(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("instance"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        if self.connection.type == ChatType.private:
            return await self.event.reply(_("You can't use this command in private chats."))

        model = await BetaModeModel.get_by_chat_iid(self.connection.db_model.iid)
        preferred_mode = model.preferred_mode if model else PreferredMode.auto

        if model and model.mode:
            current_name = _("Beta") if model.mode == CurrentMode.beta else _("Stable")
        else:
            beta_percentage = await GlobalSettings.get_by_key("beta_percentage")
            current_name = _("Stable") if not beta_percentage or int(beta_percentage.value) <= 0 else _("Unknown")

        preference_names = {
            PreferredMode.auto: _("Automatic"),
            PreferredMode.beta: _("Beta"),
            PreferredMode.stable: _("Stable"),
        }

        return await self.event.reply(
            str(
                Section(
                    KeyValue(_("Current instance"), current_name),
                    KeyValue(_("Preference"), preference_names[preferred_mode]),
                    title=_("Instance"),
                )
            )
        )
