from __future__ import annotations

from aiogram import Router
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.modules import ModuleManifest, track_scheduler_callback
from sophie_bot.modules.welcomesecurity.handlers.captcha_confirm import (
    CaptchaConfirmHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.captcha_get import CaptchaGetHandler
from sophie_bot.modules.welcomesecurity.handlers.chat_join_request import (
    ChatJoinRequestHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.enable_welcomemute import (
    EnableWelcomeMute,
)
from sophie_bot.modules.welcomesecurity.handlers.enable_ws import (
    EnableWelcomeCaptchaHandlerABC,
)
from sophie_bot.modules.welcomesecurity.handlers.legacy_button import (
    LegacyStableWSButtonRedirectHandler,
    LegacyWSButtonHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.set_security_message import (
    DelSecurityMessageHandler,
    SetSecurityMessageHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.status_overall import (
    WelcomeSecurityExpireHandler,
    WelcomeSecuritySettingsShowHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_captcha_callback_handler import (
    WelcomeSecurityCaptchaCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_cleanup_callback_handler import (
    WelcomeSecurityCleanupCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_duration_callback_handler import (
    WelcomeSecurityDurationCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_edit_callback_handler import (
    WelcomeSecurityEditCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_expiry_callback_handler import (
    WelcomeSecurityExpiryCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_home_callback_handler import (
    WelcomeSecurityHomeCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_media_callback_handler import (
    WelcomeSecurityMediaCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_message_callback_handler import (
    WelcomeSecurityMessageCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_message_input_handler import (
    WelcomeSecurityMessageInputHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_panel_handler import WelcomeSecurityPanelHandler
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_pending_callback_handler import (
    WelcomeSecurityPendingCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_reset_callback_handler import (
    WelcomeSecurityResetCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_reset_confirm_callback_handler import (
    WelcomeSecurityResetConfirmCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_set_duration_callback_handler import (
    WelcomeSecuritySetDurationCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_set_expiry_callback_handler import (
    WelcomeSecuritySetExpiryCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.handlers.welcome_security_welcome_cleanup_callback_handler import (
    WelcomeSecurityWelcomeCleanupCallbackHandler,
)
from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import (
    LockMutedUsers,
)
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="welcomesecurity")


async def setup_bot(bot_router: Router, _services: ApplicationServices) -> None:
    bot_router.message.outer_middleware(LockMutedUsers())


def setup_scheduler(scheduler: AsyncIOScheduler, services: ApplicationServices) -> None:
    scheduler.add_job(
        track_scheduler_callback(KickUnpassedUsers(services).handle, services),
        "interval",
        minutes=10,
        jobstore="ram",
    )


module_manifest = ModuleManifest(
    name="welcomesecurity",
    bot_router_factory=lambda: Router(name=router.name),
    handlers=(
        CaptchaGetHandler,
        LegacyWSButtonHandler,
        CaptchaConfirmHandler,
        ChatJoinRequestHandler,
        WelcomeSecurityPanelHandler,
        WelcomeSecurityCaptchaCallbackHandler,
        WelcomeSecurityMediaCallbackHandler,
        WelcomeSecurityCleanupCallbackHandler,
        WelcomeSecurityWelcomeCleanupCallbackHandler,
        WelcomeSecuritySetExpiryCallbackHandler,
        WelcomeSecuritySetDurationCallbackHandler,
        WelcomeSecurityResetCallbackHandler,
        WelcomeSecurityResetConfirmCallbackHandler,
        WelcomeSecurityExpiryCallbackHandler,
        WelcomeSecurityDurationCallbackHandler,
        WelcomeSecurityMessageCallbackHandler,
        WelcomeSecurityEditCallbackHandler,
        WelcomeSecurityPendingCallbackHandler,
        WelcomeSecurityHomeCallbackHandler,
        WelcomeSecurityMessageInputHandler,
        EnableWelcomeCaptchaHandlerABC,
        EnableWelcomeMute,
        SetSecurityMessageHandler,
        DelSecurityMessageHandler,
        WelcomeSecuritySettingsShowHandler,
        WelcomeSecurityExpireHandler,
        LegacyStableWSButtonRedirectHandler,
    ),
    setup_bot=setup_bot,
    setup_scheduler=setup_scheduler,
    title=l_("Welcome Security"),
    emoji="🛡️",
    description=l_("Protect your chat from bots and verify new users"),
    info=l_(
        "Welcome Security contains a bunch of tools that can help filter bots that try to join your groups, "
        "as well as make sure the new users acknowledged the chat rules before being able to speak"
    ),
)
