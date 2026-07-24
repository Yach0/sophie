from aiogram import Router
from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.op.handlers.banner import OpBannerHandler
from sophie_bot.modules.op.handlers.buttons_test import ButtonsTestHandler
from sophie_bot.modules.op.handlers.captcha import OpCaptchaHandler
from sophie_bot.modules.op.handlers.event import EventHandler
from sophie_bot.modules.op.handlers.feature_flags import FeatureFlagsHandler
from sophie_bot.modules.op.handlers.list_jobs import ListJobsHandler
from sophie_bot.modules.op.handlers.op_debug import OpDebugHandler
from sophie_bot.modules.op.handlers.op_task import OpTaskHandler
from sophie_bot.modules.op.handlers.preview_chat_summary import OpRegenerateChatSummaryHandler
from sophie_bot.modules.op.handlers.set_mode import SetModeHandler
from sophie_bot.modules.op.handlers.stats import StatsHandler, get_system_stats
from sophie_bot.modules.op.handlers.stop_jobs import StopJobsHandler

try:
    from sophie_bot.modules.op.handlers.stfu_gallery import StfuGalleryHandler as _StfuGalleryHandler

    _stfu_gallery_handlers: tuple = (_StfuGalleryHandler,)
except ImportError:
    _stfu_gallery_handlers = ()
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import health_router

api_router = APIRouter()
api_router.include_router(health_router)

__all__ = ["api_router"]

router = Router(name="op")


module_manifest = ModuleManifest(
    name="op",
    bot_router=router,
    api_router=api_router,
    handlers=(
        ListJobsHandler,
        StopJobsHandler,
        FeatureFlagsHandler,
        OpBannerHandler,
        OpCaptchaHandler,
        OpRegenerateChatSummaryHandler,
        ButtonsTestHandler,
        *_stfu_gallery_handlers,
        EventHandler,
        StatsHandler,
        OpDebugHandler,
        OpTaskHandler,
        SetModeHandler,
    ),
    title=l_("Operator"),
    emoji="👑",
    description=l_("Operator-only commands and tools"),
    info=LazyProxy(
        lambda: Doc(
            l_("Provides operator-only commands and tools for bot administration."),
            l_("Includes system stats, job management, and other administrative functions."),
        )
    ),
    exclude_public=True,
    stats=get_system_stats,
)
