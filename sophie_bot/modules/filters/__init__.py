from types import ModuleType

from aiogram import Router
from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest, get_module_manifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log

from .. import LOADED_MODULES
from .api import api_router as filters_api_router
from .enforce_middleware import EnforceFiltersMiddleware
from .handlers.action_change_setting_confirm import ActionChangeSettingConfirm
from .handlers.action_remove import ActionRemoveHandler
from .handlers.action_select import ActionSelectHandler
from .handlers.action_setting_select import ActionSettingSelectHandler
from .handlers.action_setup_confirm import ActionSetupConfirmHandler
from .handlers.actions_list import ActionsListHandler
from .handlers.actions_list_to_remove import ActionsListToRemoveHandler
from .handlers.filter_confirm import FilterConfirmHandler
from .handlers.filter_del import FilterDeleteHandler
from .handlers.filter_edit import FilterEditHandler
from .handlers.filter_new import FilterNewHandler
from .handlers.filter_save import FilterSaveHandler
from .handlers.filter_toggle_silent import FilterToggleSilentHandler
from .handlers.filters_list import FiltersListHandler
from .utils_.all_modern_actions import ALL_MODERN_ACTIONS

__all__ = (
    "api_router",
    "router",
    "pre_setup",
    "post_setup",
    "LOADED_MODULES",
)


api_router = APIRouter()
api_router.include_router(filters_api_router)


router = Router(name="filters")


async def pre_setup() -> None:
    # Enforce filters middleware
    router.message.outer_middleware(EnforceFiltersMiddleware())
    router.edited_message.outer_middleware(EnforceFiltersMiddleware())


async def post_setup(modules: dict[str, ModuleType]) -> None:
    for name, module in modules.items():
        manifest = get_module_manifest(module)

        for action_filter in manifest.modern_actions:
            log.debug("Modern filter actions: Adding new action...", name=action_filter.name, module=name)

            ALL_MODERN_ACTIONS[action_filter.name] = action_filter()


module_manifest = ModuleManifest(
    name="filters",
    bot_router=router,
    api_router=api_router,
    handlers=(
        FilterNewHandler,
        ActionsListHandler,
        ActionSetupConfirmHandler,
        ActionSelectHandler,
        FilterConfirmHandler,
        FilterSaveHandler,
        ActionSettingSelectHandler,
        FiltersListHandler,
        FilterDeleteHandler,
        ActionsListToRemoveHandler,
        ActionRemoveHandler,
        FilterEditHandler,
        ActionChangeSettingConfirm,
        FilterToggleSilentHandler,
    ),
    pre_setup=pre_setup,
    post_setup=post_setup,
    title=l_("Filters"),
    emoji="🪄",
    info=LazyProxy(
        lambda: Doc(
            l_("Filters allows to invoke different actions for different messages."),
            l_("For example muting the users when they mention crypto."),
            l_(
                "Sophie supports many different actions you can configure to automatize chat moderation in many different ways."
            ),
        )
    ),
    advertise_wiki_page=True,
)
