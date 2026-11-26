from datetime import UTC, datetime

from aiogram import Router
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.modules import ModuleManifest, track_scheduler_callback
from sophie_bot.modules.antispam.handlers import (
    AntispamIncidentCallbackHandler,
    AntispamModeHandler,
    AntispamStatusHandler,
    AntispamThirdPartyHandler,
    AntispamTrustHandler,
)
from sophie_bot.modules.antispam.middleware import AntispamMiddleware
from sophie_bot.modules.antispam.registry import ProviderRegistry
from sophie_bot.modules.antispam.service import cleanup_expired_antispam_messages
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import lazy_gettext as l_


async def initialize(services: ApplicationServices) -> None:
    if services.antispam is None:
        services.antispam = ProviderRegistry(services.config, services.redis)


async def setup_bot(router: Router, _services: ApplicationServices) -> None:
    middleware = AntispamMiddleware()
    router.message.outer_middleware(middleware)
    router.edited_message.outer_middleware(middleware)


def setup_scheduler(scheduler: AsyncIOScheduler, services: ApplicationServices) -> None:
    if services.antispam is not None:
        scheduler.add_job(
            track_scheduler_callback(services.antispam.sync_bulk, services),
            "interval",
            hours=6,
            id="antispam_bulk_sync",
            jobstore="ram",
            next_run_time=datetime.now(UTC),
            max_instances=1,
            coalesce=True,
        )
    scheduler.add_job(
        track_scheduler_callback(lambda: cleanup_expired_antispam_messages(services), services),
        "interval",
        seconds=60,
        id="antispam_message_cleanup",
        jobstore="ram",
        max_instances=1,
        coalesce=True,
    )


module_manifest = ModuleManifest(
    name="antispam",
    title=l_("Anti-spam"),
    emoji="🛡",
    description=l_("Protect groups with built-in and third-party anti-spam."),
    bot_router_factory=lambda: Router(name="antispam"),
    handlers=(
        AntispamStatusHandler,
        AntispamModeHandler,
        AntispamThirdPartyHandler,
        AntispamTrustHandler,
        AntispamIncidentCallbackHandler,
    ),
    initialize=initialize,
    setup_bot=setup_bot,
    setup_scheduler=setup_scheduler,
)
