from types import ModuleType

from aiogram import Router

from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_WELCOME_SECURITY_BUTTON_PREFIX,
    LegacyButtonAction,
    register_legacy_button_actions,
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
    WelcomeSecuritySettingsShowHandler,
)
from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import (
    LockMutedUsers,
)
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.services.scheduler import scheduler
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="welcomesecurity")


register_legacy_button_actions(LegacyButtonAction("welcomesecurity", LEGACY_WELCOME_SECURITY_BUTTON_PREFIX))


async def pre_setup() -> None:
    router.message.outer_middleware(LockMutedUsers())


async def post_setup(_modules: dict[str, ModuleType]) -> None:
    if SOPHIE_MODE == "scheduler":
        scheduler.add_job(KickUnpassedUsers().handle, "interval", minutes=10, jobstore="ram")


module_manifest = ModuleManifest(
    name="welcomesecurity",
    bot_router=router,
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
        LegacyStableWSButtonRedirectHandler,
    ),
    pre_setup=pre_setup,
    post_setup=post_setup,
    title=l_("Welcome Security"),
    emoji="🛡️",
    description=l_("Protect your chat from bots and verify new users"),
    info=l_(
        "Welcome Security contains a bunch of tools that can help filter bots that tries to join your groups, as well as make sure the new users acknowledged the chat rules before being able to speak"
    ),
)
