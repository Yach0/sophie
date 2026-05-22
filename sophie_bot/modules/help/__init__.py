from types import ModuleType

from aiogram import F, Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from ...filters.cmd import CMDFilter
from ...filters.user_status import IsOP
from ...utils.logger import log
from .callbacks import PMHelpModule
from .handlers.help_group import HelpGroupHandler
from .handlers.op import OpCMDSList
from .handlers.pm_modules import PMModuleHelp, PMModulesList
from .handlers.set_lang_legacy import set_lang_cb
from .handlers.start_group import StartGroupHandler
from .handlers.start_pm import StartPMHandler
from .stats import module_stats
from .utils.extract_info import HELP_MODULES, gather_module_help

router = Router(name="info")


async def pre_setup() -> None:
    router.callback_query.register(PMModuleHelp, PMHelpModule.filter())

    router.message.register(OpCMDSList, CMDFilter("op_cmds"), IsOP(True))

    router.callback_query.register(set_lang_cb, F.data == "lang_btn")


async def post_setup(modules: dict[str, ModuleType]) -> None:
    for name, module in modules.items():
        if module_help := await gather_module_help(module):
            if name in HELP_MODULES:
                log.debug(f"Module {name} already in help modules, merging")
                module_help.handlers = HELP_MODULES[name].handlers + module_help.handlers

            HELP_MODULES[name] = module_help


module_manifest = ModuleManifest(
    name="help",
    bot_router=router,
    handlers=(StartPMHandler, HelpGroupHandler, PMModulesList, StartGroupHandler),
    pre_setup=pre_setup,
    post_setup=post_setup,
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
