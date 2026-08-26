from types import ModuleType

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.ai.handlers.ai_addfilter import AIFilterAddHandler
from sophie_bot.modules.ai.handlers.ai_cmd import AiCmd
from sophie_bot.modules.ai.handlers.aimode import AIModeSelectCallback, AIModeSetting
from sophie_bot.modules.ai.handlers.aimoderator import AIModeratorCategoryToggle, AIModeratorSetting
from sophie_bot.modules.ai.handlers.autotranslate_setting import (
    AIAutotrans,
    AutoTranslateLanguageHandler,
)
from sophie_bot.modules.ai.handlers.feature_setting import AIChatSummariesSetting, AINoteTitlesSetting
from sophie_bot.modules.ai.handlers.op_catalog import OpAIModel, OpAIModels, OpAIProvider, OpAIProviders
from sophie_bot.modules.ai.handlers.op_prices import OpAIPricesHandler
from sophie_bot.modules.ai.handlers.op_quota import ResetQuota, SetQuota
from sophie_bot.modules.ai.handlers.op_stats import OpAIStatsHandler
from sophie_bot.modules.ai.handlers.pm import (
    AiPmHandle,
    AiPmHelpMode,
    AiPmInitialize,
    AiPmNormalMode,
    AiPmStop,
)
from sophie_bot.modules.ai.handlers.reply import AiReplyHandler
from sophie_bot.modules.ai.handlers.research import ResearchCmd
from sophie_bot.modules.ai.handlers.reset_context import AIContextReset
from sophie_bot.modules.ai.handlers.translate import AiTranslate
from sophie_bot.modules.ai.handlers.usage import AiUsage
from sophie_bot.modules.ai.magic_handlers.modern_action import AIReplyAction
from sophie_bot.modules.ai.middlewares.ai_moderator import AiModeratorMiddleware
from sophie_bot.modules.ai.middlewares.ai_status import AiStatusMiddleware
from sophie_bot.modules.ai.middlewares.ai_timeout import AiTimeoutMiddleware
from sophie_bot.modules.ai.middlewares.auto_translate import AiAutoTranslateMiddleware
from sophie_bot.modules.ai.middlewares.cache_bot_messages import (
    CacheBotMessagesMiddleware,
)
from sophie_bot.modules.ai.middlewares.cache_user_messages import (
    CacheUserMessagesMiddleware,
)
from sophie_bot.modules.ai.schedules.generate_chat_summaries import GenerateChatSummaries
from sophie_bot.modules.ai.texts import AI_POLICY
from sophie_bot.modules.ai.utils.ai_catalog import load_catalog
from sophie_bot.services.scheduler import scheduler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router

__all__ = [
    "api_router",
    "pre_setup",
    "router",
]

router = Router(name="ai")


async def pre_setup() -> None:
    await load_catalog()

    router.message.outer_middleware(CacheUserMessagesMiddleware())
    router.message.middleware(CacheBotMessagesMiddleware())
    router.message.outer_middleware(AiModeratorMiddleware())
    router.message.outer_middleware(AiStatusMiddleware())
    router.message.outer_middleware(AiTimeoutMiddleware())
    router.message.outer_middleware(AiAutoTranslateMiddleware())


async def post_setup(_modules: dict[str, ModuleType]) -> None:
    if SOPHIE_MODE == "scheduler":
        scheduler.add_job(
            GenerateChatSummaries().handle,
            "cron",
            hour=23,
            minute=30,
            timezone="UTC",
            jobstore="ram",
        )


module_manifest = ModuleManifest(
    name="ai",
    bot_router=router,
    api_router=api_router,
    handlers=(
        OpAIStatsHandler,
        OpAIPricesHandler,
        OpAIProviders,
        OpAIProvider,
        OpAIModels,
        OpAIModel,
        AIModeSetting,
        AIModeSelectCallback,
        AIModeratorSetting,
        AIModeratorCategoryToggle,
        AIAutotrans,
        AutoTranslateLanguageHandler,
        AIChatSummariesSetting,
        AINoteTitlesSetting,
        AIFilterAddHandler,
        AiPmInitialize,
        AIContextReset,
        ResearchCmd,
        AiTranslate,
        AiUsage,
        SetQuota,
        ResetQuota,
        AiReplyHandler,
        AiPmNormalMode,
        AiPmHelpMode,
        AiPmStop,
        AiPmHandle,
        AiCmd,
    ),
    pre_setup=pre_setup,
    post_setup=post_setup,
    title=l_("Sophie AI"),
    emoji=AI_EMOJI,
    description=l_("Rainbow sparkles and shininess"),
    info=LazyProxy(
        lambda: Doc(
            l_("Sophie supports quite a few ways to use AI features."),
            l_("From a simple chat-bot, to the automatic translator. Have fun."),
            " ",
            AI_POLICY,
            l_("Please note that each chat has a limited monthly AI quota."),
            l_("Use /aiusage to check your remaining quota."),
        )
    ),
    modern_actions=(AIReplyAction,),
)
