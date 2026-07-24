from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram.types import Message
from beanie import PydanticObjectId
from normality import normalize
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from regex import regex
from stfu_tg import Template

from sophie_bot.constants import AI_FILTER_NEW_USER_MAX_AGE_HOURS
from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_filters_model, resolve_chat_service_tier
from sophie_bot.modules.ai.utils.ai_tasks import AIStructuredTask, run_structured_task
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.filters.utils_.ai_filter_schema import AIFilterResponseSchema
from sophie_bot.modules.filters.utils_.extract_content import extract_message_content
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type
from sophie_bot.services.redis import aredis
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import FeatureType, get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

_REGEX_TIMEOUT_SECONDS = 0.5


def match_regex_handler(message_text: str, pattern: str) -> bool:
    """Match message text against a regex pattern."""
    try:
        return bool(regex.search(pattern, message_text, timeout=_REGEX_TIMEOUT_SECONDS))
    except TimeoutError:
        raise SophieException(
            f'The regex in the filter with pattern "{pattern}" is taking too long to execute. '
            f"Sophie will not function properly until it will be removed."
        )


def match_exact_handler(message_text: str, text: str) -> bool:
    """Match message text exactly against the provided text."""
    # For exact match, we need to compare the whole strings
    return message_text == text


def match_contains_handler(text: str, handler: str) -> bool:
    """Check if a message text contains the specified text."""

    normalized_handler = normalize(handler)
    normalized_text = normalize(text)

    if not normalized_handler or not normalized_text:
        return False

    return normalized_handler in normalized_text


def match_word_handler(text: str, handler: str) -> bool:
    """Whole-word or phrase match, concise but readable variable names."""
    normalized_text = normalize(text)
    normalized_handler = normalize(handler)
    if not normalized_text or not normalized_handler:
        return False

    text_tokens = normalized_text.split()
    handler_tokens = normalized_handler.split()
    handler_length = len(handler_tokens)

    return (handler_length == 1 and handler_tokens[0] in text_tokens) or any(
        text_tokens[i : i + handler_length] == handler_tokens for i in range(len(text_tokens) - handler_length + 1)
    )


def _get_ai_filter_daily_chat_limit_key(chat_tid: int, now: datetime) -> str:
    return f"ai_filter_daily_limit:{chat_tid}:{now.strftime('%Y%m%d')}"


def _get_ai_filter_daily_user_limit_key(chat_tid: int, user_tid: int, now: datetime) -> str:
    return f"ai_filter_daily_limit:{chat_tid}:user:{user_tid}:{now.strftime('%Y%m%d')}"


def _seconds_until_next_utc_day(now: datetime) -> int:
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((next_day - now).total_seconds()), 1)


async def _feature_int(feature: FeatureType, chat_tid: int | None) -> int:
    value = await get_value(feature, chat_tid=chat_tid)
    return int(value) if isinstance(value, int) else 0


async def consume_ai_filter_daily_quota(chat_tid: int, user_tid: int | None = None) -> bool:
    now = datetime.now(timezone.utc)
    chat_rate_limit_key = _get_ai_filter_daily_chat_limit_key(chat_tid, now)
    daily_ttl = _seconds_until_next_utc_day(now)
    chat_limit = await _feature_int("ai_filter_daily_chat_limit", chat_tid)
    user_limit = await _feature_int("ai_filter_daily_user_limit", chat_tid)

    async with aredis.pipeline() as pipe:
        pipe.incr(chat_rate_limit_key)
        pipe.expire(chat_rate_limit_key, daily_ttl)
        if user_tid is not None:
            user_rate_limit_key = _get_ai_filter_daily_user_limit_key(chat_tid, user_tid, now)
            pipe.incr(user_rate_limit_key)
            pipe.expire(user_rate_limit_key, daily_ttl)
        results = await pipe.execute()

    chat_daily_count = int(results[0])
    user_daily_count = int(results[2]) if user_tid is not None else 0

    if chat_limit > 0 and chat_daily_count > chat_limit:
        return False

    return not (user_tid is not None and user_limit > 0 and user_daily_count > user_limit)


async def _is_within_new_user_message_limit(user_in_group: UserInGroupModel, chat_tid: int) -> bool:
    message_limit = await _feature_int("ai_filter_new_user_message_limit", chat_tid)
    if message_limit <= 0:
        return False

    seen_messages = user_in_group.ai_filter_seen_messages
    if seen_messages >= message_limit:
        log.debug(
            "match_ai_handler: user exceeded AI filter message limit",
            ai_filter_seen_messages=seen_messages,
            message_limit=message_limit,
        )
        return False

    user_in_group.ai_filter_seen_messages = seen_messages + 1
    await user_in_group.save()
    return True


async def match_ai_handler(
    message: Message,
    prompt: str,
    user_in_group: UserInGroupModel | None = None,
    chat_iid: PydanticObjectId | None = None,
) -> bool:
    """
    Match a message against an AI-powered filter.

    The model is resolved via ``get_chat_filters_model``: the ai_filter_handler_model flag when set,
    otherwise the model the chat's AI mode uses for filters.

    Supports text, photos, videos (thumbnail), and stickers.

    Args:
        message: The Telegram message to evaluate
        prompt: The user-provided prompt describing when to trigger the filter
        user_in_group: The user in group database model to check for join date

    Returns:
        bool: True if the message matches the filter criteria
    """
    chat_tid = getattr(getattr(message, "chat", None), "id", None)

    # Check if AI filters feature is enabled
    if not await is_enabled("ai_filters", chat_tid=chat_tid):
        log.debug("match_ai_handler: ai_filters feature flag is disabled, skipping AI evaluation")
        return False

    # Limit AI filters to users who joined recently
    if not user_in_group:
        log.debug("match_ai_handler: no user-in-group model, skipping AI evaluation")
        return False

    joined_after_threshold = datetime.now(timezone.utc) - timedelta(hours=AI_FILTER_NEW_USER_MAX_AGE_HOURS)
    first_saw = user_in_group.first_saw
    if first_saw.tzinfo is None:
        first_saw = first_saw.replace(tzinfo=timezone.utc)
    if first_saw < joined_after_threshold:
        log.debug(
            "match_ai_handler: user joined before AI threshold, skipping AI evaluation",
            first_saw=user_in_group.first_saw,
        )
        return False

    if not await _is_within_new_user_message_limit(user_in_group, message.chat.id):
        return False

    user_tid = message.from_user.id if message.from_user else None
    if not await consume_ai_filter_daily_quota(message.chat.id, user_tid=user_tid):
        log.debug(
            "match_ai_handler: daily AI filter limit reached, skipping AI evaluation",
            chat_tid=message.chat.id,
            user_tid=user_tid,
        )
        return False

    try:
        # Extract message content (text and optional image)
        text_content, image_data = await extract_message_content(message)

        # Build the AI message history
        history = AIMessageHistory()

        # Add system prompt
        system_prompt = _(
            "You are a content moderation assistant. Evaluate whether the provided message content "
            "matches the filter criteria. Be precise and objective in your assessment."
        )
        history.add_system(system_prompt)

        # Build user prompt with filter criteria
        user_prompt_text = Template(
            _("Filter criteria: {criteria}\n\nMessage content: {content}"),
            criteria=prompt,
            content=text_content or _("(no text content)"),
        ).to_html()

        # Add text to prompt
        history.prompt = [user_prompt_text]

        # Add image if present
        if image_data:
            history.prompt.append(
                BinaryContent(
                    media_type="image/jpeg",
                    data=image_data,
                )
            )

        # Run AI evaluation
        model = await get_chat_filters_model(chat_iid, chat_tid)
        service_tier = await resolve_chat_service_tier(AIModelPurpose.filters, chat_iid, chat_tid)

        result = await run_structured_task(
            AIStructuredTask(
                output_type=AIFilterResponseSchema,
                model_settings=OpenRouterModelSettings(openrouter_reasoning={"effort": "low"}),
            ),
            model,
            history,
            user_tracking_id=chat_iid,
            chat_tid=chat_tid,
            service_tier=service_tier,
        )

        log.debug(
            "match_ai_handler: AI evaluation",
            prompt=prompt,
            matches=result.output.matches,
            reasoning=result.output.reasoning,
        )

        return result.output.matches

    except Exception as e:
        log.warning("match_ai_handler: AI filter evaluation failed", error=str(e))
        # On error, don't trigger the filter to avoid false positives
        return False


async def match_filter_handler(
    message: Message,
    handler: str,
    user_in_group: UserInGroupModel | None = None,
    enable_lock_types: bool = True,
    chat_iid: PydanticObjectId | None = None,
) -> bool:
    """Match a message against different types of handlers (regex, exact, contains, AI)."""
    # AI-powered handler
    if handler.startswith("ai:"):
        log.debug(f"match_filter_handler: ai: {handler}")
        prompt = handler[3:]
        return await match_ai_handler(message, prompt, user_in_group=user_in_group, chat_iid=chat_iid)

    if enable_lock_types and is_supported_lock_type(handler):
        from sophie_bot.modules.locks.utils.detect_lock import check_locks

        return bool(await check_locks(message, {handler}))

    if not (message_text := message.caption or message.text or ""):
        return False

    # Regex support
    if handler.startswith("re:"):
        log.debug(f"match_filter_handler: regex: {handler}")
        pattern = handler[3:]
        return match_regex_handler(message_text, pattern)

    # Exact text match
    if handler.startswith("exact:"):
        log.debug(f"match_filter_handler: exact: {handler}")
        text = handler[6:]
        return match_exact_handler(message_text, text)

    # Whole word or phrase match (no regex)
    if handler.startswith("word:"):
        log.debug(f"match_filter_handler: word: {handler}")
        word_or_phrase = handler[5:]
        return match_word_handler(message_text, word_or_phrase)

    # Contains text match (default behavior)
    log.debug(f"match_filter_handler: contains: {handler}")
    return match_contains_handler(message_text, handler)
