from types import ModuleType

from aiogram import Router
from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest, get_module_manifest
from sophie_bot.shared.action_registry import ALL_MODERN_ACTIONS
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log

from .. import LOADED_MODULES
from .api import api_router as filters_api_router
from .enforce_middleware import EnforceFiltersMiddleware
from .filter_wizard import (
    FilterWizardCallbackHandler,
    FilterWizardInputCleanupHandler,
    FilterWizardInputHandler,
    FilterWizardToggleHandler,
)
from .handlers.filter_del import FilterDeleteHandler
from .handlers.filter_edit import FilterEditHandler
from .handlers.filter_new import FilterNewHandler
from .handlers.filters_list import (
    FilterDeleteConfirmHandler,
    FilterDeletePromptHandler,
    FilterEditFromListHandler,
    FiltersListHandler,
    FiltersPageHandler,
)

__all__ = ("LOADED_MODULES", "api_router", "post_setup", "pre_setup", "router")

api_router = APIRouter()
api_router.include_router(filters_api_router)
router = Router(name="filters")


async def pre_setup() -> None:
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
        FilterEditHandler,
        FiltersListHandler,
        FiltersPageHandler,
        FilterEditFromListHandler,
        FilterDeletePromptHandler,
        FilterDeleteConfirmHandler,
        FilterDeleteHandler,
        FilterWizardToggleHandler,
        FilterWizardCallbackHandler,
        FilterWizardInputHandler,
        FilterWizardInputCleanupHandler,
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
