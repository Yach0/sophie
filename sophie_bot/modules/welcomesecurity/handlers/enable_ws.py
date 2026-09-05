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
from sophie_bot.db.models.greetings import WELCOMESECURITY_EXPIRE_DEFAULT_TIME
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.status_handler import StatusHandlerABC
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

type WelcomeCaptchaStatus = timedelta | Literal[False]


@flags.help(
    description=l_("Shows / changes Welcome Captcha and its expiry time."),
    args={"NewStatus": TextArg(l_("?New status or expiry time"))},
)
class EnableWelcomeCaptchaHandlerABC(StatusHandlerABC[WelcomeCaptchaStatus]):
    header_text = l_("Welcome Captcha")
    change_command = "welcomecaptcha"
    change_args = "on / off / 12h / 2d / 1w"

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {
            "new_status": OptionalArg(
                OrArg(
                    ActionTimeArg(l_("? Expiry time"), min=timedelta(minutes=1)),
                    BooleanArg(l_("? New status")),
                )
            )
        }

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("welcomecaptcha", "enablewelcomecaptcha")), UserRestricting(admin=True)

    def status_text(self, status_data: WelcomeCaptchaStatus) -> Element | str:
        if status_data is False:
            return _("Disabled")

        return Template(
            _("Enabled, unverified members expire after {time}"),
            time=Italic(format_timedelta(status_data, locale=self.current_locale.replace("-", "_"))),
        )

    async def get_status(self) -> WelcomeCaptchaStatus:
        db_model = await GreetingsModel.get_by_chat_iid(self.connection.db_model.iid)
        if not db_model.welcome_security or not db_model.welcome_security.enabled:
            return False

        return db_model.welcome_security.expire or WELCOMESECURITY_EXPIRE_DEFAULT_TIME

    async def set_status(self, new_status: WelcomeCaptchaStatus) -> None:
        db_model = await GreetingsModel.get_by_chat_iid(self.connection.db_model.iid)
        expire = new_status if new_status is not False else None
        await db_model.set_status_welcomesecurity(new_status is not False, expire)

    async def handle(self) -> Any:
        new_status: timedelta | bool | None = self.data.get("new_status")
        if new_status is None:
            return await self.display_current_status()

        if new_status is True:
            db_model = await GreetingsModel.get_by_chat_iid(self.connection.db_model.iid)
            configured_expiry = (
                db_model.welcome_security.expire
                if db_model.welcome_security and db_model.welcome_security.expire
                else WELCOMESECURITY_EXPIRE_DEFAULT_TIME
            )
            return await self.change_status(configured_expiry)

        return await self.change_status(new_status)
