from __future__ import annotations

from aiogram import Router
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.modules import ModuleManifest, track_scheduler_callback
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_WELCOME_SECURITY_BUTTON_PREFIX,
    LegacyButtonAction,
)
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
    legacy_buttons=(LegacyButtonAction("welcomesecurity", LEGACY_WELCOME_SECURITY_BUTTON_PREFIX),),
    title=l_("Welcome Security"),
    emoji="🛡️",
    description=l_("Protect your chat from bots and verify new users"),
    info=l_(
        "Welcome Security contains a bunch of tools that can help filter bots that tries to join your groups, as well as make sure the new users acknowledged the chat rules before being able to speak"
    ),
)
