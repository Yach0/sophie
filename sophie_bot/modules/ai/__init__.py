from types import ModuleType

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.constants import AI_EMOJI
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modes import SOPHIE_MODE
from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.ai.handlers.ai_addfilter import AIFilterAddHandler
from sophie_bot.modules.ai.handlers.ai_cmd import AiCmd
from sophie_bot.modules.ai.handlers.ai_moderator_setting import AIModerator
from sophie_bot.modules.ai.handlers.aiprovider import (
    AIProviderSelectCallback,
    AIProviderSetting,
    AIProviderSettingAlt,
)
from sophie_bot.modules.ai.handlers.autotranslate_setting import (
    AIAutotrans,
)
from sophie_bot.modules.ai.handlers.enable_setting import EnableAI
from sophie_bot.modules.ai.handlers.filter import get_filter
from sophie_bot.modules.ai.handlers.op_prices import op_ai_prices_handler
from sophie_bot.modules.ai.handlers.op_quota import ResetQuota, SetQuota
from sophie_bot.modules.ai.handlers.op_stats import op_ai_stats_handler
from sophie_bot.modules.ai.handlers.playground import (
    AIPlaygroundCmd,
    AIPlaygroundModelSelectCallback,
)
from sophie_bot.modules.ai.handlers.pm import AiPmHandle, AiPmInitialize, AiPmStop
from sophie_bot.modules.ai.handlers.reply import AiReplyHandler
from sophie_bot.modules.ai.handlers.reset_context import AIContextReset
from sophie_bot.modules.ai.handlers.translate import AiTranslate, text_or_reply
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
from sophie_bot.services.scheduler import scheduler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router

__all__ = [
    "router",
    "api_router",
    "pre_setup",
]

router = Router(name="ai")


def _register_context_handlers() -> None:
    router.message.register(AIContextReset, *AIContextReset.filters())
    router.message.register(AIContextReset, *AIContextReset.filters_alt())


def _register_translation_handlers() -> None:
    router.message.register(AiTranslate, *AiTranslate.filters(), flags={"args": text_or_reply})
    router.message.outer_middleware(AiAutoTranslateMiddleware())


def _register_usage_handlers() -> None:
    router.message.register(AiUsage, *AiUsage.filters())
    router.message.register(op_ai_stats_handler, CMDFilter("op_aistats"), IsOP(True))
    router.message.register(op_ai_prices_handler, CMDFilter("op_aiprices"), IsOP(True))


def _register_quota_handlers() -> None:
    router.message.register(SetQuota, *SetQuota.filters())
    router.message.register(ResetQuota, *ResetQuota.filters())


def _register_chat_handlers() -> None:
    router.message.register(AiReplyHandler, *AiReplyHandler.filters())
    router.message.register(AiPmStop, *AiPmStop.filters())
    router.message.register(AiPmHandle, *AiPmHandle.filters())
    router.message.register(AiCmd, *AiCmd.filters())


async def pre_setup() -> None:
    router.message.outer_middleware(CacheUserMessagesMiddleware())
    router.message.middleware(CacheBotMessagesMiddleware())

    # AI Moderator
    router.message.outer_middleware(AiModeratorMiddleware())

    # AI typing status (outer) — runs before timeout, so typing shows during AI work
    router.message.outer_middleware(AiStatusMiddleware())
    # AI timeout (outer) — prevents AI handlers from hanging indefinitely
    router.message.outer_middleware(AiTimeoutMiddleware())

    _register_context_handlers()
    _register_translation_handlers()
    _register_usage_handlers()
    _register_quota_handlers()
    _register_chat_handlers()


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
        EnableAI,
        AIModerator,
        AIAutotrans,
        AIFilterAddHandler,
        AIProviderSetting,
        AIProviderSettingAlt,
        AIProviderSelectCallback,
        AIPlaygroundCmd,
        AIPlaygroundModelSelectCallback,
        AiPmInitialize,
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
    filter_actions=get_filter(),
    modern_actions=(AIReplyAction,),
)
