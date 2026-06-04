from aiogram import Router

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import lazy_gettext as l_

from ...filters.admin_rights import UserRestricting
from ...filters.cmd import CMDFilter
from ...filters.message_status import HasArgs
from .handlers.admincache import ResetAdminCache
from .handlers.beta_state import set_preferred_mode, show_beta_state
from .handlers.cancel import CancelState
from .handlers.cancel_callback import CallbackActionCancelHandler, CancelCallbackHandler, TypedCancelCallbackHandler
from .handlers.op_settings import ResetBetaChats, SetBetaPercentage
from .stats import beta_stats

router = Router(name="troubleshooters")


async def pre_setup() -> None:
    # Beta
    router.message.register(
        set_preferred_mode, CMDFilter(("setmode", "enablebeta")), HasArgs(True), UserRestricting(admin=True)
    )
    router.message.register(show_beta_state, CMDFilter(("setmode", "enablebeta")), UserRestricting(admin=True))


module_manifest = ModuleManifest(
    name="troubleshooters",
    bot_router=router,
    handlers=(
        CancelCallbackHandler,
        TypedCancelCallbackHandler,
        CallbackActionCancelHandler,
        ResetAdminCache,
        SetBetaPercentage,
        ResetBetaChats,
        CancelState,
    ),
    pre_setup=pre_setup,
    title=l_("Troubleshooters"),
    emoji="🧰",
    description=l_("Tools for fixing problems and issues"),
    stats=beta_stats,
)
