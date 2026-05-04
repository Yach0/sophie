from aiogram import Router
from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules.op.handlers.Banner import OpBannerHandler
from sophie_bot.modules.op.handlers.ButtonsTest import ButtonsTestHandler
from sophie_bot.modules.op.handlers.Captcha import OpCaptchaHandler
from sophie_bot.modules.op.handlers.KillSwitch import KillSwitchHandler
from sophie_bot.modules.op.handlers.ListJobs import ListJobsHandler
from sophie_bot.modules.op.handlers.PreviewChatSummary import (
    OpPreviewChatSummaryHandler,
    OpRegenerateChatSummaryHandler,
)
from sophie_bot.modules.op.handlers.StopJobs import StopJobsHandler
from sophie_bot.modules.op.handlers.TestSummarizeHistory import OpTestSummarizeHistoryHandler
from sophie_bot.modules.op.handlers.event import EventHandler
from sophie_bot.modules.op.handlers.stats import StatsHandler, get_system_stats
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_
from .api import health_router

api_router = APIRouter()
api_router.include_router(health_router)

__all__ = ["api_router"]

router = Router(name="op")

__module_name__ = l_("Operator")
__module_emoji__ = "👑"
__module_description__ = l_("Operator-only commands and tools")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Provides operator-only commands and tools for bot administration."),
        l_("Includes system stats, job management, and other administrative functions."),
    )
)

__exclude_public__ = True

__handlers__ = (
    ListJobsHandler,
    StopJobsHandler,
    KillSwitchHandler,
    OpBannerHandler,
    OpCaptchaHandler,
    OpTestSummarizeHistoryHandler,
    OpPreviewChatSummaryHandler,
    OpRegenerateChatSummaryHandler,
    ButtonsTestHandler,
    EventHandler,
    StatsHandler,
)
__stats__ = get_system_stats
