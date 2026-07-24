from datetime import timedelta
from typing import Any, Literal

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import ActionTimeArg, BooleanArg, OptionalArg, OrArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from babel.dates import format_timedelta
from stfu_tg import Italic, Template
from stfu_tg.doc import Element

from sophie_bot.db.models import GreetingsModel
from sophie_bot.db.models.greetings import WELCOMEMUTE_DEFAULT_TIME
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.status_handler import StatusHandlerABC
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

type WelcomeMuteStatus = timedelta | Literal[False]


@flags.help(
    description=l_("Shows / changes the state of Welcome Restrict (Media restricting)."),
    args={"NewStatus": TextArg(l_("?New status or restrict time"))},
)
class EnableWelcomeMute(StatusHandlerABC[WelcomeMuteStatus]):
    header_text = l_("Welcome Mute (Automatic new users media restricting)")
    change_command = "welcomerestrict"
    change_args = "on / off / 12h / 2d / 1w"

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"new_status": OptionalArg(OrArg(ActionTimeArg(l_("? Action time")), BooleanArg("?New status")))}

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("welcomerestrict"), UserRestricting(admin=True)

    def status_text(self, status_data: WelcomeMuteStatus) -> Element | str:
        if status_data is False:
            return _("Disabled")

        return Template(
            _("Enabled, set to {time}"),
            time=Italic(format_timedelta(status_data, locale=self.current_locale.replace("-", "_"))),
        )

    async def get_status(self) -> WelcomeMuteStatus:
        db_model = await GreetingsModel.get_by_chat_iid(self.connection.db_model.iid)

        if not db_model.welcome_mute or not db_model.welcome_mute.enabled or not db_model.welcome_mute.time:
            return False

        return db_model.welcome_mute.time

    async def set_status(self, new_status: WelcomeMuteStatus) -> None:
        db_model = await GreetingsModel.get_by_chat_iid(self.connection.db_model.iid)

        time: timedelta | None = new_status if new_status is not False else None
        await db_model.set_status_welcomemute(time is not None, time)

    async def handle(self) -> Any:
        new_status: timedelta | bool | None = self.data.get("new_status")

        if new_status is None:
            return await self.display_current_status()

        return await self.change_status(WELCOMEMUTE_DEFAULT_TIME if new_status is True else new_status)
