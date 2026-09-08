from __future__ import annotations

from aiogram import Router
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.modules import ModuleManifest, track_scheduler_callback
from sophie_bot.modules.communities.handlers.cban import CommunityBanHandler
from sophie_bot.modules.communities.handlers.uncban import CommunityUnbanHandler
from sophie_bot.modules.communities.middlewares.check_cban import CommunityBanMiddleware
from sophie_bot.modules.communities.schedules.process_bans import ProcessCommunityBans
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="communities")


async def setup_bot(router: Router, _services: ApplicationServices) -> None:
    router.message.outer_middleware(CommunityBanMiddleware())


def setup_scheduler(scheduler: AsyncIOScheduler, services: ApplicationServices) -> None:
    scheduler.add_job(
        track_scheduler_callback(ProcessCommunityBans(services).handle, services),
        "interval",
        seconds=10,
        jobstore="ram",
    )


module_manifest = ModuleManifest(
    name="communities",
    bot_router_factory=lambda: Router(name=router.name),
    handlers=(
        CommunityBanHandler,
        CommunityUnbanHandler,
    ),
    setup_bot=setup_bot,
    setup_scheduler=setup_scheduler,
    title=l_("Communities"),
    emoji="🌐",
    description=l_("Manage bans across Telegram communities"),
    info=l_(
        "Communities let you ban a user from every chat of a Telegram community Sophie is in "
        "with a single command, and keep the ban enforced for users who post or join later."
    ),
)
