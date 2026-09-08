from __future__ import annotations

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import list_all
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.help_group import HelpGroupHandler
from .handlers.op import OpCMDSList
from .handlers.pm_modules import PMModuleHelp, PMModulesList
from .handlers.set_lang_legacy import SetLangLegacyHandler
from .handlers.start_group import StartGroupHandler
from .handlers.start_pm import StartPMHandler
from .stats import module_stats
from .utils.extract_info import build_help_catalog

router = Router(name="help")


async def initialize(services: ApplicationServices) -> None:
    await build_help_catalog(
        services.modules,
        await list_all(redis=services.redis),
    )


module_manifest = ModuleManifest(
    name="help",
    bot_router_factory=lambda: Router(name=router.name),
    handlers=(
        StartPMHandler,
        HelpGroupHandler,
        PMModulesList,
        PMModuleHelp,
        OpCMDSList,
        SetLangLegacyHandler,
        StartGroupHandler,
    ),
    initialize=initialize,
    title=l_("Help"),
    emoji="ℹ️",
    description=l_("Provides helpful information"),
    info=LazyProxy(
        lambda: Doc(
            l_("Provides help and documentation for all bot commands and features."),
            l_("Includes command lists, usage instructions, and feature explanations."),
        )
    ),
)


__all__ = ["module_stats"]
