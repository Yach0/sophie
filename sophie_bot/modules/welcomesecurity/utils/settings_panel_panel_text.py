from __future__ import annotations

from babel.dates import format_timedelta
from redis.asyncio import Redis
from stfu_tg import Doc, KeyValue, Section

from sophie_bot.db.models import GreetingsModel
from sophie_bot.db.models.greetings import WELCOMEMUTE_DEFAULT_TIME
from sophie_bot.modules.welcomesecurity.handlers.status_overall import _effective_expiry
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _


async def _panel_text(settings: GreetingsModel, chat_tid: int, locale: str, *, redis: Redis) -> Doc:
    captcha_enabled = bool(settings.welcome_security and settings.welcome_security.enabled)
    service_enabled = await is_enabled("welcomecaptcha", chat_tid=chat_tid, redis=redis)
    expiry_enabled = await is_enabled("welcomecaptcha_autokick", chat_tid=chat_tid, redis=redis)
    captcha_status = _("Enabled") if captcha_enabled and service_enabled else _("Disabled")
    if captcha_enabled and not service_enabled:
        captcha_status = _("Disabled by service configuration")
    media_enabled = bool(settings.welcome_mute and settings.welcome_mute.enabled and settings.welcome_mute.time)
    duration = (settings.welcome_mute.time if settings.welcome_mute else None) or WELCOMEMUTE_DEFAULT_TIME
    return Doc(
        Section(
            KeyValue(_("CAPTCHA"), captcha_status),
            KeyValue(
                _("CAPTCHA expiry"), format_timedelta(_effective_expiry(settings), locale=locale.replace("-", "_"))
            ),
            KeyValue(
                _("Unanswered CAPTCHA"),
                _("Kick members; decline join requests")
                if expiry_enabled
                else _("Timeout removal disabled by service configuration"),
            ),
            KeyValue(_("Media restriction"), _("Enabled") if media_enabled else _("Disabled")),
            KeyValue(_("Restriction duration"), format_timedelta(duration, locale=locale.replace("-", "_"))),
            KeyValue(_("Security message"), _("Custom") if settings.security_note else _("Default")),
            KeyValue(
                _("Join/leave message cleanup"),
                _("Enabled") if settings.clean_service and settings.clean_service.enabled else _("Disabled"),
            ),
            KeyValue(
                _("Previous welcome message cleanup"),
                _("Enabled") if settings.clean_welcome and settings.clean_welcome.enabled else _("Disabled"),
            ),
            title=_("Welcome Security"),
        ),
        _("Settings changes do not ask verified users to solve CAPTCHA again."),
    )
