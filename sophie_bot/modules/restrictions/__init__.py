from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.restrictions.actions.ban import BanModernAction
from sophie_bot.modules.restrictions.actions.kick import KickModernAction
from sophie_bot.modules.restrictions.actions.mute import MuteModernAction
from sophie_bot.modules.restrictions.handlers import (
    BanUserHandler,
    KickUserHandler,
    MuteUserHandler,
    SilentBanUserHandler,
    SilentKickUserHandler,
    SilentMuteUserHandler,
    SilentTempBanUserHandler,
    SilentTempMuteUserHandler,
    TempBanUserHandler,
    TempMuteUserHandler,
    UnbanUserHandler,
    UnmuteUserHandler,
)
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="restrictions")


module_manifest = ModuleManifest(
    name="restrictions",
    bot_router=router,
    handlers=(
        KickUserHandler,
        SilentKickUserHandler,
        BanUserHandler,
        SilentBanUserHandler,
        TempBanUserHandler,
        SilentTempBanUserHandler,
        MuteUserHandler,
        SilentMuteUserHandler,
        TempMuteUserHandler,
        SilentTempMuteUserHandler,
        UnmuteUserHandler,
        UnbanUserHandler,
    ),
    title=l_("Restrictions"),
    emoji="🛑",
    description=l_("Manage user restrictions in chats"),
    info=LazyProxy(
        lambda: Doc(
            l_("Provides commands to restrict users in chats."),
            l_("Includes ban, kick, mute, and temporary restrictions."),
        )
    ),
    modern_actions=(KickModernAction, BanModernAction, MuteModernAction),
)
