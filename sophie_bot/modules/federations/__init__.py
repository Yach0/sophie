from __future__ import annotations

from aiogram import Router
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.modules import ModuleManifest, track_scheduler_callback
from sophie_bot.modules.federations.api import api_router as federations_api_router
from sophie_bot.modules.federations.handlers.accept_transfer import AcceptTransferHandler
from sophie_bot.modules.federations.handlers.admins import FederationAdminsHandler
from sophie_bot.modules.federations.handlers.ban import FederationBanHandler
from sophie_bot.modules.federations.handlers.banlist import FederationBanListHandler
from sophie_bot.modules.federations.handlers.chats import FederationChatsHandler
from sophie_bot.modules.federations.handlers.create import CreateFederationHandler
from sophie_bot.modules.federations.handlers.delete import FederationDeleteCallbackHandler, FederationDeleteHandler
from sophie_bot.modules.federations.handlers.demote import FederationDemoteHandler
from sophie_bot.modules.federations.handlers.fcheck_group import FederationCheckGroupHandler
from sophie_bot.modules.federations.handlers.fcheck_pm import FederationCheckPMHandler
from sophie_bot.modules.federations.handlers.import_banlist import FederationImportHandler
from sophie_bot.modules.federations.handlers.info import FederationInfoHandler
from sophie_bot.modules.federations.handlers.join import JoinFederationHandler
from sophie_bot.modules.federations.handlers.leave import LeaveFederationHandler
from sophie_bot.modules.federations.handlers.logs import SetFederationLogHandler, UnsetFederationLogHandler
from sophie_bot.modules.federations.handlers.promote import FederationPromoteHandler
from sophie_bot.modules.federations.handlers.rename import FederationRenameHandler
from sophie_bot.modules.federations.handlers.subscribe import SubscribeFederationHandler, UnsubscribeFederationHandler
from sophie_bot.modules.federations.handlers.transfer import TransferOwnershipHandler
from sophie_bot.modules.federations.handlers.unban import FederationUnbanHandler
from sophie_bot.modules.federations.middlewares.check_fban import FedBanMiddleware
from sophie_bot.modules.federations.schedules.cleanup_tasks import CleanupOldTasks
from sophie_bot.modules.federations.schedules.process_bans import ProcessFederationBans
from sophie_bot.modules.federations.schedules.process_exports import ProcessFederationExports
from sophie_bot.modules.federations.schedules.process_imports import ProcessFederationImports
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import lazy_gettext as l_

api_router = federations_api_router
router = Router(name="federations")


async def setup_bot(bot_router: Router, _services: ApplicationServices) -> None:
    bot_router.message.outer_middleware(FedBanMiddleware())


def setup_scheduler(scheduler: AsyncIOScheduler, services: ApplicationServices) -> None:
    scheduler.add_job(
        track_scheduler_callback(ProcessFederationBans(services).handle, services),
        "interval",
        seconds=10,
        jobstore="ram",
    )
    scheduler.add_job(
        track_scheduler_callback(ProcessFederationImports(services).handle, services),
        "interval",
        seconds=30,
        jobstore="ram",
    )
    scheduler.add_job(
        track_scheduler_callback(ProcessFederationExports(services).handle, services),
        "interval",
        seconds=30,
        jobstore="ram",
    )
    # This also reaps orphaned tasks; users should not wait hours for a failure.
    scheduler.add_job(
        track_scheduler_callback(CleanupOldTasks(services).handle, services),
        "interval",
        minutes=5,
        jobstore="ram",
    )


module_manifest = ModuleManifest(
    name="federations",
    bot_router_factory=lambda: Router(name=router.name),
    api_router_factory=lambda: api_router,
    handlers=(
        CreateFederationHandler,
        JoinFederationHandler,
        LeaveFederationHandler,
        FederationInfoHandler,
        FederationBanHandler,
        FederationUnbanHandler,
        FederationBanListHandler,
        FederationCheckGroupHandler,
        FederationCheckPMHandler,
        TransferOwnershipHandler,
        AcceptTransferHandler,
        SetFederationLogHandler,
        UnsetFederationLogHandler,
        SubscribeFederationHandler,
        UnsubscribeFederationHandler,
        FederationImportHandler,
        FederationRenameHandler,
        FederationDeleteHandler,
        FederationDeleteCallbackHandler,
        FederationChatsHandler,
        FederationAdminsHandler,
        FederationPromoteHandler,
        FederationDemoteHandler,
    ),
    setup_bot=setup_bot,
    setup_scheduler=setup_scheduler,
    title=l_("Federations"),
    emoji="🏛",
    description=l_("Manage federations across multiple chats"),
    info=l_(
        "Federations allow you to manage multiple chats as a group. "
        "You can ban users across all chats in a federation, "
        "subscribe to other federations, and manage permissions."
    ),
)
