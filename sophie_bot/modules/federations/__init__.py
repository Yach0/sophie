from types import ModuleType

from aiogram import Router

from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules import ModuleManifest
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
from sophie_bot.services.scheduler import scheduler
from sophie_bot.utils.i18n import lazy_gettext as l_

api_router = federations_api_router
router = Router(name="federations")


async def pre_setup() -> None:
    router.message.outer_middleware(FedBanMiddleware())


async def post_setup(_modules: dict[str, ModuleType]) -> None:
    if SOPHIE_MODE == "scheduler":
        scheduler.add_job(ProcessFederationBans().handle, "interval", seconds=10, jobstore="ram")
        scheduler.add_job(ProcessFederationImports().handle, "interval", seconds=30, jobstore="ram")
        scheduler.add_job(ProcessFederationExports().handle, "interval", seconds=30, jobstore="ram")
        # Every 5 minutes rather than hourly: this job also reaps orphaned tasks, and a user
        # staring at a stuck "Propagating…" reply shouldn't wait hours to be told it failed.
        scheduler.add_job(CleanupOldTasks().handle, "interval", minutes=5, jobstore="ram")


module_manifest = ModuleManifest(
    name="federations",
    bot_router=router,
    api_router=api_router,
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
    pre_setup=pre_setup,
    post_setup=post_setup,
    title=l_("Federations"),
    emoji="🏛",
    description=l_("Manage federations across multiple chats"),
    info=l_(
        "Federations allow you to manage multiple chats as a group. "
        "You can ban users across all chats in a federation, "
        "subscribe to other federations, and manage permissions."
    ),
)
