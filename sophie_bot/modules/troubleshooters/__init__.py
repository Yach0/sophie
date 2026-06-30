from aiogram import Router

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.admincache import ResetAdminCache
from .handlers.cancel import CancelState
from .handlers.cancel_callback import CallbackActionCancelHandler, CancelCallbackHandler, TypedCancelCallbackHandler
from .handlers.op_settings import ResetBetaChats, SetBetaPercentage
from .stats import beta_stats

router = Router(name="troubleshooters")


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
    title=l_("Troubleshooters"),
    emoji="🧰",
    description=l_("Tools for fixing problems and issues"),
    stats=beta_stats,
)
