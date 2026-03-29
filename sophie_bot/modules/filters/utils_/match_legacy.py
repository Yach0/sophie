from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram.types import Message
from normality import normalize
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from regex import regex
from stfu_tg import Template

from sophie_bot.constants import AI_FILTER_DAILY_LIMIT_PER_CHAT, AI_FILTER_NEW_USER_MAX_AGE_HOURS
from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.modules.ai.utils.ai_models import FILTER_HANDLER_MODEL
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate_schema
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.modules.filters.utils_.ai_filter_schema import AIFilterResponseSchema
from sophie_bot.modules.filters.utils_.extract_content import extract_message_content
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type
from sophie_bot.services.redis import aredis
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


def match_regex_handler(message_text: str, pattern: str) -> bool:
    """Match message text against a regex pattern."""
    try:
        return bool(regex.search(pattern, message_text))
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


def _get_ai_filter_daily_limit_key(chat_tid: int, now: datetime) -> str:
    return f"ai_filter_daily_limit:{chat_tid}:{now.strftime('%Y%m%d')}"


def _seconds_until_next_utc_day(now: datetime) -> int:
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((next_day - now).total_seconds()), 1)


async def consume_ai_filter_daily_quota(chat_tid: int) -> bool:
    now = datetime.now(timezone.utc)
    rate_limit_key = _get_ai_filter_daily_limit_key(chat_tid, now)
    daily_ttl = _seconds_until_next_utc_day(now)

    async with aredis.pipeline() as pipe:
        await pipe.incr(rate_limit_key)
        await pipe.expire(rate_limit_key, daily_ttl)
        daily_count, _ = await pipe.execute()

    return int(daily_count) <= AI_FILTER_DAILY_LIMIT_PER_CHAT


async def match_ai_handler(
    message: Message, prompt: str, user_in_group: UserInGroupModel | None = None, chat_iid: object | None = None
) -> bool:
    """
    Match a message against AI-powered filter using Mistral Pixtral model.

    Supports text, photos, videos (thumbnail), and stickers.

    Args:
        message: The Telegram message to evaluate
        prompt: The user-provided prompt describing when to trigger the filter
        user_in_group: The user in group database model to check for join date

    Returns:
        bool: True if the message matches the filter criteria
    """
    # Check if AI filters feature is enabled
    if not await is_enabled("ai_filters"):
        log.debug("match_ai_handler: ai_filters feature flag is disabled, skipping AI evaluation")
        return False

    # Limit AI filters to users who joined recently
    if user_in_group:
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

    if not await consume_ai_filter_daily_quota(message.chat.id):
        log.debug(
            "match_ai_handler: daily AI filter limit reached for chat, skipping AI evaluation",
            chat_tid=message.chat.id,
            daily_limit=AI_FILTER_DAILY_LIMIT_PER_CHAT,
        )
        return False

    try:
        # Extract message content (text and optional image)
        text_content, image_data = await extract_message_content(message)

        # Build the AI message history
        history = NewAIMessageHistory()

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
        kwargs = {}
        model = FILTER_HANDLER_MODEL()
        kwargs["model_settings"] = OpenRouterModelSettings(openrouter_reasoning={"effort": "low"})

        result = await new_ai_generate_schema(
            history, AIFilterResponseSchema, model, user_tracking_id=chat_iid, **kwargs
        )

        log.debug("match_ai_handler: AI evaluation", prompt=prompt, matches=result.matches, reasoning=result.reasoning)

        return result.matches

    except Exception as e:
        log.error("match_ai_handler: AI filter evaluation failed", error=str(e))
        # On error, don't trigger the filter to avoid false positives
        return False


async def match_legacy_handler(
    message: Message,
    handler: str,
    user_in_group: UserInGroupModel | None = None,
    enable_lock_types: bool = True,
    chat_iid: object | None = None,
) -> bool:
    """Match a message against different types of handlers (regex, exact, contains, AI)."""
    # AI-powered handler
    if handler.startswith("ai:"):
        log.debug(f"match_legacy_handler: ai: {handler}")
        prompt = handler[3:]
        return await match_ai_handler(message, prompt, user_in_group=user_in_group, chat_iid=chat_iid)

    if enable_lock_types and is_supported_lock_type(handler):
        from sophie_bot.modules.locks.utils.detect_lock import check_locks

        return bool(await check_locks(message, {handler}))

    if not (message_text := message.caption or message.text or ""):
        return False

    # Legacy regex support
    if handler.startswith("re:"):
        log.debug(f"match_legacy_handler: regex: {handler}")
        pattern = handler[3:]
        return match_regex_handler(message_text, pattern)

    # Exact text match
    if handler.startswith("exact:"):
        log.debug(f"match_legacy_handler: exact: {handler}")
        text = handler[6:]
        return match_exact_handler(message_text, text)

    # Whole word or phrase match (no regex)
    if handler.startswith("word:"):
        log.debug(f"match_legacy_handler: word: {handler}")
        word_or_phrase = handler[5:]
        return match_word_handler(message_text, word_or_phrase)

    # Contains text match (default behavior)
    log.debug(f"match_legacy_handler: contains: {handler}")
    return match_contains_handler(message_text, handler)
