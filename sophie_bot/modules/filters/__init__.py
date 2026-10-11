from aiogram import Router
from fastapi import APIRouter
from stfu_tg import Doc, Template

from sophie_bot.modules import ModuleManifest
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

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

__all__ = ("api_router", "router")

api_router = APIRouter()
api_router.include_router(filters_api_router)
router = Router(name="filters")


async def setup_bot(bot_router: Router, _services: ApplicationServices) -> None:
    bot_router.message.outer_middleware(EnforceFiltersMiddleware())
    bot_router.edited_message.outer_middleware(EnforceFiltersMiddleware())


module_manifest = ModuleManifest(
    name="filters",
    bot_router_factory=lambda: Router(name=router.name),
    api_router_factory=lambda: api_router,
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
    setup_bot=setup_bot,
    title=l_("Filters"),
    emoji="🪄",
    info=LazyProxy(
        lambda: Doc(
            l_("Filters allows to invoke different actions for different messages."),
            l_("For example muting the users when they mention crypto."),
            l_(
                "Sophie supports many different actions you can configure to automatize chat moderation in many different ways."
            ),
            l_("Without a prefix, filters match text contained in the normalized message; punctuation is not literal."),
            Template(
                l_("{prefix} matches a whole normalized word or phrase."),
                prefix="word:",
            ),
            Template(
                l_("{prefix} matches the whole literal message."),
                prefix="exact:",
            ),
            Template(l_("{prefix} uses a regular expression."), prefix="re:"),
            Template(
                l_("{prefix} asks AI whether the message meets a criterion."),
                prefix="ai:",
            ),
            Template(
                l_("Lock-type handlers use Locks detectors; {command} lists them."),
                command="/lockable",
            ),
        )
    ),
    advertise_wiki_page=True,
)
