from datetime import timedelta
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityExpireCB
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

CAPTCHA_EXPIRY_OPTIONS = (
    timedelta(hours=12),
    timedelta(days=1),
    WELCOMESECURITY_EXPIRE_DEFAULT_TIME,
    timedelta(weeks=1),
)


def _effective_expiry(db_item: GreetingsModel) -> timedelta:
    if db_item.welcome_security and db_item.welcome_security.expire:
        return db_item.welcome_security.expire
    return WELCOMESECURITY_EXPIRE_DEFAULT_TIME


def _settings_text(db_item: GreetingsModel, locale: str) -> Doc:
    captcha_enabled = bool(db_item.welcome_security and db_item.welcome_security.enabled)
    mute_enabled = bool(db_item.welcome_mute and db_item.welcome_mute.enabled)
    normalized_locale = locale.replace("-", "_")

    return Doc(
        KeyValue(_("Captcha"), _("Enabled") if captcha_enabled else _("Disabled")),
        KeyValue(
            _("Captcha expiry"),
            Template(
                _("Pending members are removed after {time}"),
                time=format_timedelta(_effective_expiry(db_item), locale=normalized_locale),
            ),
        ),
        KeyValue(
            _("Media restriction"),
            (
                Template(
                    _("Enabled, on {time}"),
                    time=format_timedelta(
                        (db_item.welcome_mute.time if db_item.welcome_mute else None) or WELCOMEMUTE_DEFAULT_TIME,
                        locale=normalized_locale,
                    ),
                )
                if mute_enabled
                else _("Disabled")
            ),
        ),
        Template(_("Use {cmd} to control Welcome Captcha"), cmd=Italic("/welcomecaptcha")),
        Template(_("Use {cmd} to control Media restriction"), cmd=Italic("/welcomerestrict")),
        Template(_("Use {cmd} to set a custom Welcome Security message"), cmd=Italic("/setwelcomesecurity")),
        Template(_("Check out {cmd} to learn more about Welcome settings."), cmd=Italic("/help")),
    )


def _expiry_keyboard(db_item: GreetingsModel, locale: str) -> InlineKeyboardMarkup:
    current_expiry = _effective_expiry(db_item)
    normalized_locale = locale.replace("-", "_")
    buttons = InlineKeyboardBuilder()
    for expiry in CAPTCHA_EXPIRY_OPTIONS:
        selected = "✅ " if expiry == current_expiry else ""
        buttons.add(
            InlineKeyboardButton(
                text=f"{selected}{format_timedelta(expiry, locale=normalized_locale)}",
                callback_data=WelcomeSecurityExpireCB(seconds=int(expiry.total_seconds())).pack(),
            )
        )
    buttons.adjust(2)
    return buttons.as_markup()


@flags.help(description=l_("Shows Welcome Security settings"))
@flags.disableable(name="welcomesecurity")
class WelcomeSecuritySettingsShowHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter("welcomesecurity"), UserRestricting(admin=True))

    async def handle(self) -> Any:
        connection = self.connection

        db_item: GreetingsModel = await GreetingsModel.get_by_chat_iid(connection.db_model.iid)

        await self.event.reply(
            str(_settings_text(db_item, self.current_locale)),
            reply_markup=_expiry_keyboard(db_item, self.current_locale),
        )

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
        )


class WelcomeSecurityExpireHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return WelcomeSecurityExpireCB.filter(), UserRestricting(admin=True)

    async def handle(self) -> Any:
        seconds = self.callback_data.seconds
        allowed_seconds = {int(expiry.total_seconds()) for expiry in CAPTCHA_EXPIRY_OPTIONS}
        if seconds not in allowed_seconds:
            return await self.event.answer(_("This expiry option is no longer available."), show_alert=True)

        db_item = await GreetingsModel.get_by_chat_iid(self.connection.db_model.iid)
        await db_item.set_status_welcomesecurity(
            bool(db_item.welcome_security and db_item.welcome_security.enabled),
            timedelta(seconds=seconds),
        )
        await self.event.answer(_("Captcha expiry updated."))
        return await self.edit_text(
            _settings_text(db_item, self.current_locale),
            reply_markup=_expiry_keyboard(db_item, self.current_locale),
        )
