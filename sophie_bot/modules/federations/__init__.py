from aiogram import Router

from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules.federations.api import api_router as federations_api_router
from sophie_bot.modules.federations.handlers.accept_transfer import AcceptTransferHandler
from sophie_bot.modules.federations.handlers.admins import FederationAdminsHandler
from sophie_bot.modules.federations.handlers.ban import FederationBanHandler
from sophie_bot.modules.federations.handlers.banlist import FederationBanListHandler
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler as FederationCommandHandler
from sophie_bot.modules.federations.handlers.chats import FederationChatsHandler
from sophie_bot.modules.federations.handlers.create import CreateFederationHandler
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
from sophie_bot.utils.i18n import lazy_gettext as l_

__module_name__ = l_("Federations")
__module_emoji__ = "🏛"
__module_description__ = l_("Manage federations across multiple chats")
__module_info__ = l_(
    "Federations allow you to manage multiple chats as a group. "
    "You can ban users across all chats in a federation, "
    "subscribe to other federations, and manage permissions."
)

api_router = federations_api_router
router = Router(name="federations")

__handlers__ = (
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
    FederationChatsHandler,
    FederationAdminsHandler,
    FederationPromoteHandler,
    FederationDemoteHandler,
)


async def __pre_setup__():
    router.message.outer_middleware(FedBanMiddleware())


async def __post_setup__(_):
    if SOPHIE_MODE == "scheduler":
        from sophie_bot.modules.federations.schedules.cleanup_exports import CleanupOldExports
        from sophie_bot.modules.federations.schedules.process_exports import ProcessFederationExports
        from sophie_bot.modules.federations.schedules.process_imports import ProcessFederationImports
        from sophie_bot.services.scheduler import scheduler

        scheduler.add_job(ProcessFederationImports().handle, "interval", seconds=30, jobstore="ram")
        scheduler.add_job(ProcessFederationExports().handle, "interval", seconds=30, jobstore="ram")
        scheduler.add_job(CleanupOldExports().handle, "interval", hours=6, jobstore="ram")
