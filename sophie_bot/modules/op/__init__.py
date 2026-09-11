from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.op.handlers.banner import OpBannerHandler
from sophie_bot.modules.op.handlers.buttons_test import ButtonsTestHandler
from sophie_bot.modules.op.handlers.captcha import OpCaptchaHandler
from sophie_bot.modules.op.handlers.event import EventHandler
from sophie_bot.modules.op.handlers.feature_flags import FeatureFlagsHandler
from sophie_bot.modules.op.handlers.op_debug import OpDebugHandler
from sophie_bot.modules.op.handlers.op_task import OpTaskHandler
from sophie_bot.modules.op.handlers.preview_chat_summary import OpRegenerateChatSummaryHandler
from sophie_bot.modules.op.handlers.set_mode import SetModeHandler
from sophie_bot.modules.op.handlers.stats import StatsHandler, get_system_stats
from sophie_bot.modules.op.handlers.stfu_gallery import StfuGalleryHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="op")


module_manifest = ModuleManifest(
    name="op",
    bot_router_factory=lambda: Router(name=router.name),
    handlers=(
        FeatureFlagsHandler,
        OpBannerHandler,
        OpCaptchaHandler,
        OpRegenerateChatSummaryHandler,
        ButtonsTestHandler,
        StfuGalleryHandler,
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
