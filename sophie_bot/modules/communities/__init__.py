from __future__ import annotations

from types import ModuleType

from aiogram import Router

from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.communities.handlers.cban import CommunityBanHandler
from sophie_bot.modules.communities.handlers.uncban import CommunityUnbanHandler
from sophie_bot.modules.communities.middlewares.check_cban import CommunityBanMiddleware
from sophie_bot.modules.communities.schedules.process_bans import ProcessCommunityBans
from sophie_bot.services.scheduler import scheduler
from sophie_bot.utils.i18n import lazy_gettext as l_


router = Router(name="communities")


async def pre_setup() -> None:
    router.message.outer_middleware(CommunityBanMiddleware())


async def post_setup(_modules: dict[str, ModuleType]) -> None:
    if SOPHIE_MODE == "scheduler":
        scheduler.add_job(ProcessCommunityBans().handle, "interval", seconds=10, jobstore="ram")


module_manifest = ModuleManifest(
    name="communities",
    bot_router=router,
    handlers=(
        CommunityBanHandler,
        CommunityUnbanHandler,
    ),
    pre_setup=pre_setup,
    post_setup=post_setup,
    title=l_("Communities"),
    emoji="🌐",
    description=l_("Manage bans across Telegram communities"),
    info=l_(
        "Communities let you ban a user from every chat of a Telegram community Sophie is in "
        "with a single command, and keep the ban enforced for users who post or join later."
    ),
)
