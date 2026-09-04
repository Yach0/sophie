from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from babel.dates import format_timedelta
from stfu_tg import Bold, Doc, Italic, KeyValue, Template, Title

from sophie_bot.db.models import GreetingsModel, RulesModel
from sophie_bot.db.models.greetings import (
    WELCOMEMUTE_DEFAULT_TIME,
    WELCOMESECURITY_EXPIRE_DEFAULT_TIME,
)
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.greetings.default_welcome import get_default_security_message
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Shows Welcome Security settings"))
@flags.disableable(name="welcomesecurity")
class WelcomeSecuritySettingsShowHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter("welcomesecurity"), UserRestricting(admin=True))

    async def handle(self) -> Any:
        connection = self.connection

        db_item: GreetingsModel = await GreetingsModel.get_by_chat_iid(connection.db_model.iid)

        captcha_enabled = db_item.welcome_security and db_item.welcome_security.enabled
        mute_enabled = db_item.welcome_mute and db_item.welcome_mute.enabled

        doc = Doc(
            # Captcha
            KeyValue(
                _("Captcha"),
                (
                    Template(
                        l_("Enabled, expires in {time}"),
                        time=format_timedelta(
                            (db_item.welcome_security.expire if db_item.welcome_security else None)
                            or WELCOMESECURITY_EXPIRE_DEFAULT_TIME,
                            locale=self.current_locale,
                        ),
                    )
                    if captcha_enabled
                    else l_("Disabled")
                ),
            ),
            # Mute
            KeyValue(
                _("Media restriction"),
                (
                    Template(
                        l_("Enabled, on {time}"),
                        time=format_timedelta(
                            (db_item.welcome_mute.time if db_item.welcome_mute else None) or WELCOMEMUTE_DEFAULT_TIME,
                            locale=self.current_locale,
                        ),
                    )
                    if mute_enabled
                    else l_("Disabled")
                ),
            ),
            Template(_("Use {cmd} to control Welcome Captcha"), cmd=Italic("/enablewelcomecaptcha")),
            Template(_("Use {cmd} to control Media restriction"), cmd=Italic("/welcomerestrict")),
            Template(_("Use {cmd} to set a custom Welcome Security message"), cmd=Italic("/setwelcomesecurity")),
            Template(_("Check out {cmd} to learn more about Welcome settings."), cmd=Italic("/help")),
        )
        await self.event.reply(str(doc))

        title = Bold(Title(_("Welcome Security message")))

        rules = await RulesModel.get_rules(connection.db_model.iid)
        additional_fillings = {"rules": rules.text or "" if rules else _("No chat rules, have fun!")}

        welcome = db_item.security_note or get_default_security_message()

        return await send_saveable(
            self.event,
            self.event.chat.id,
            welcome,
            title=title,
            raw=False,
            reply_to=self.event.message_id,
            additional_fillings=additional_fillings,
            connection=connection,
            owner_chat_tid=self.event.chat.id,
        )
