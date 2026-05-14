from sophie_bot.modules.antiflood.bot import handlers, setup_bot_transport
from sophie_bot.modules.antiflood.module import (
    __module_description__,
    __module_emoji__,
    __module_name__,
    api_router,
    module_manifest,
    router,
)

__all__ = [
    "router",
    "api_router",
    "__module_name__",
    "__module_emoji__",
    "__module_description__",
    "handlers",
    "setup_bot_transport",
    "module_manifest",
]
