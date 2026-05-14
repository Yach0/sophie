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

__module_name__ = l_("Restrictions")
__module_emoji__ = "🛑"
__module_description__ = l_("Manage user restrictions in chats")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Provides commands to restrict users in chats."),
        l_("Includes ban, kick, mute, and temporary restrictions."),
    )
)

router = Router(name="restrictions")

__modern_actions__ = (KickModernAction, BanModernAction, MuteModernAction)

__handlers__ = (
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
)

module_manifest = ModuleManifest(
    name="restrictions",
    bot_router=router,
    handlers=__handlers__,
    metadata={
        "name": __module_name__,
        "emoji": __module_emoji__,
        "description": __module_description__,
        "info": __module_info__,
        "modern_actions": __modern_actions__,
    },
)
