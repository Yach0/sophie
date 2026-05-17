from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message, ReactionTypeEmoji
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserContent

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.metrics import track_ai_conversation, track_ai_usage
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_chatbot_reply import (
    CHATBOT_TOOLS,
    _build_chatbot_header,
    _build_reply_doc,
    _build_system_prompt,
    _truncate_output,
)
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_default_model
from sophie_bot.modules.ai.utils.ai_models import get_proactive_replies_model
from sophie_bot.modules.ai.utils.ai_quota import check_quota
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.cache_messages import MessageType, cache_message, get_cached_messages
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate, new_ai_generate_schema_with_result
from sophie_bot.modules.ai.utils.new_message_history import AIUserMessageFormatter, NewAIMessageHistory
from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import FeatureType, get_service_tier, get_value, is_enabled
from sophie_bot.utils.logger import log

ProactiveActionName = Literal["none", "react", "answer"]

_ELIGIBLE_KEY_TEMPLATE = "ai:proactive:{chat_tid}:eligible"
_LOCK_KEY_TEMPLATE = "ai:proactive:{chat_tid}:lock"
_LOCK_TTL_SECONDS = 120
_PROCESSED_TTL_SECONDS = 86400
_DEFAULT_BATCH_SIZE = 15
_DEFAULT_WINDOW_SECONDS = 600
_DEFAULT_MAX_ANSWERS = 2
_DEFAULT_MAX_REACTIONS = 4
_DEFAULT_MIN_MESSAGES = 15


class ProactiveAction(BaseModel):
    action: ProactiveActionName
    message_id: int | None = None
    emoji: str | None = None
    reason: str | None = None


class ProactiveDecision(BaseModel):
    actions: list[ProactiveAction] = Field(default_factory=list)


class ProactiveReplySettings(BaseModel):
    batch_size: int = _DEFAULT_BATCH_SIZE
    window_seconds: int = _DEFAULT_WINDOW_SECONDS
    max_answers: int = _DEFAULT_MAX_ANSWERS
    max_reactions: int = _DEFAULT_MAX_REACTIONS
    min_messages: int = _DEFAULT_MIN_MESSAGES


async def _feature_int(feature: str, chat_tid: int, default: int, minimum: int = 1) -> int:
    value = await get_value(cast(FeatureType, feature), chat_tid=chat_tid)
    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed_value, minimum)


async def _get_settings(chat_tid: int) -> ProactiveReplySettings:
    batch_size = await _feature_int("ai_proactive_replies_batch_size", chat_tid, _DEFAULT_BATCH_SIZE)
    window_seconds = await _feature_int("ai_proactive_replies_window_seconds", chat_tid, _DEFAULT_WINDOW_SECONDS)
    max_answers = await _feature_int("ai_proactive_replies_max_answers", chat_tid, _DEFAULT_MAX_ANSWERS, minimum=0)
    max_reactions = await _feature_int(
        "ai_proactive_replies_max_reactions", chat_tid, _DEFAULT_MAX_REACTIONS, minimum=0
    )
    min_messages = await _feature_int("ai_proactive_replies_min_messages", chat_tid, _DEFAULT_MIN_MESSAGES)
    return ProactiveReplySettings(
        batch_size=batch_size,
        window_seconds=window_seconds,
        max_answers=max_answers,
        max_reactions=max_reactions,
        min_messages=min(min_messages, batch_size),
    )


def _eligible_key(chat_tid: int) -> str:
    return _ELIGIBLE_KEY_TEMPLATE.format(chat_tid=chat_tid)


def _lock_key(chat_tid: int) -> str:
    return _LOCK_KEY_TEMPLATE.format(chat_tid=chat_tid)


def _is_candidate(message: MessageType) -> bool:
    return bool(
        message.eligible_for_proactive_ai
        and not message.handled_by_ai
        and not message.has_ai_command
        and not message.reply_to_is_sophie_ai
        and message.user_id != CONFIG.bot_id
    )


def _target_by_message_id(messages: tuple[MessageType, ...]) -> dict[int, MessageType]:
    return {message.message_id: message for message in messages}


def _render_messages_for_prompt(messages: tuple[MessageType, ...]) -> str:
    rendered_messages: list[str] = []
    for message in messages:
        username = message.username or str(message.user_id)
        reply_part = ""
        if message.reply_to_message_id:
            reply_username = message.reply_to_username or str(message.reply_to_user_id or "unknown")
            reply_part = f" | replies_to={message.reply_to_message_id} ({reply_username})"
        rendered_messages.append(
            " | ".join(
                (
                    f"message_id={message.message_id}",
                    f"time={message.created_at.isoformat() if message.created_at else 'unknown'}",
                    f"user={username}{reply_part}",
                    f"text={message.text}",
                )
            )
        )
    return "\n".join(rendered_messages)


def _build_decision_prompt(messages: tuple[MessageType, ...], settings: ProactiveReplySettings) -> str:
    rendered_messages = _render_messages_for_prompt(messages)
    return "\n".join(
        (
            "You are Sophie AI deciding whether to naturally participate in a Telegram group chat.",
            "Prefer no action. Only act when the context is particularly good for Sophie.",
            "Allowed actions are: none, react, answer.",
            f"You may answer at most {settings.max_answers} messages.",
            f"You may react to at most {settings.max_reactions} messages.",
            "For react actions, use exactly one common emoji in the emoji field.",
            "Never answer commands, messages already handled by AI, messages containing /ai, or replies to Sophie AI.",
            "Pick message_id values only from the provided messages.",
            "If nothing is worth Sophie joining, return an empty actions list or only a none action.",
            "Recent messages:",
            rendered_messages,
        )
    )


def _build_decision_history(messages: tuple[MessageType, ...], settings: ProactiveReplySettings) -> NewAIMessageHistory:
    history = NewAIMessageHistory()
    history.message_history = [
        ModelRequest(
            parts=[
                SystemPromptPart(
                    content=(
                        "Return structured JSON only. Be conservative and avoid spam. "
                        "Sophie should stay silent unless her reaction or answer would feel natural."
                    )
                )
            ]
        )
    ]
    history.prompt = [_build_decision_prompt(messages, settings)]
    return history


def _limit_actions(decision: ProactiveDecision, settings: ProactiveReplySettings) -> tuple[ProactiveAction, ...]:
    answer_count = 0
    reaction_count = 0
    limited_actions: list[ProactiveAction] = []
    for action in decision.actions:
        if action.action == "none":
            continue
        if action.action == "answer":
            if answer_count >= settings.max_answers:
                continue
            answer_count += 1
            limited_actions.append(action)
        if action.action == "react":
            if reaction_count >= settings.max_reactions:
                continue
            reaction_count += 1
            limited_actions.append(action)
    return tuple(limited_actions)


async def _get_recent_candidates(chat_tid: int, settings: ProactiveReplySettings) -> tuple[MessageType, ...]:
    now = datetime.now(timezone.utc)
    messages = await get_cached_messages(chat_tid, now=now)
    min_created_at = now - timedelta(seconds=settings.window_seconds)
    candidates = tuple(
        message
        for message in messages
        if message.created_at and message.created_at >= min_created_at and _is_candidate(message)
    )
    return candidates[-settings.batch_size :]


async def _track_eligible_message(chat_tid: int, message: Message, settings: ProactiveReplySettings) -> int:
    key = _eligible_key(chat_tid)
    cutoff_score = (datetime.now(timezone.utc) - timedelta(seconds=settings.window_seconds)).timestamp()
    async with aredis.pipeline(transaction=True) as pipe:
        await pipe.zadd(key, {str(message.message_id): message.date.timestamp()})  # type: ignore[misc]
        await pipe.zremrangebyscore(key, 0, cutoff_score)  # type: ignore[misc]
        await pipe.expire(key, _PROCESSED_TTL_SECONDS, lt=True)
        await pipe.zcard(key)  # type: ignore[misc]
        results = await pipe.execute()
    return int(results[-1])


async def _clear_tracked_messages(chat_tid: int, messages: tuple[MessageType, ...]) -> None:
    if not messages:
        return
    key = _eligible_key(chat_tid)
    await aredis.zrem(key, *(str(message.message_id) for message in messages))


async def _acquire_lock(chat_tid: int) -> bool:
    return bool(await cast(Awaitable[bool | None], aredis.set(_lock_key(chat_tid), "1", ex=_LOCK_TTL_SECONDS, nx=True)))


async def _release_lock(chat_tid: int) -> None:
    await aredis.delete(_lock_key(chat_tid))


async def _generate_decision(
    chat: ChatModel, chat_tid: int, messages: tuple[MessageType, ...], settings: ProactiveReplySettings
) -> ProactiveDecision:
    model = await get_proactive_replies_model(chat_tid)
    service_tier = await get_service_tier("ai_proactive_replies_service_tier", chat_tid=chat_tid)
    history = _build_decision_history(messages, settings)
    result = await new_ai_generate_schema_with_result(
        history,
        ProactiveDecision,
        model,
        user_tracking_id=chat.iid,
        session_id=f"proactive:{chat.iid}",
        service_tier=service_tier,
    )
    if result.usage:
        track_ai_usage(model, result.usage)
        await charge_ai_usage(chat.iid, AI_FEATURE_CHATBOT, model, result.usage)
    return result.output


async def _react_to_message(chat_tid: int, target_message: MessageType, emoji: str | None) -> None:
    if not emoji:
        return
    try:
        await bot.set_message_reaction(
            chat_id=chat_tid,
            message_id=target_message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        log.debug("Proactive AI reaction skipped", chat_id=chat_tid, message_id=target_message.message_id, error=error)


async def _build_answer_history(chat_tid: int, chat: ChatModel, target_message: MessageType) -> NewAIMessageHistory:
    history = NewAIMessageHistory()
    system_prompt = await _build_system_prompt(chat.iid, chat_tid, target_message.text)
    history.add_chatbot_system_msg(additional=system_prompt.to_md())
    await history.add_from_cache(chat_tid)
    username = target_message.username or str(target_message.user_id)
    prompt_text = AIUserMessageFormatter.user_message(target_message.text, username)
    history.prompt = [cast(UserContent, prompt_text)]
    return history


async def _answer_message(chat_tid: int, chat: ChatModel, target_message: MessageType) -> None:
    connection = ChatConnection(
        type=chat.type,
        is_connected=False,
        tid=chat.tid,
        title=chat.first_name_or_title,
        db_model=chat,
    )
    model = await get_chat_default_model(chat.iid)
    service_tier = await get_service_tier("ai_chatbot_service_tier", chat_tid=chat_tid)
    history = await _build_answer_history(chat_tid, chat, target_message)
    agent_kwargs = {"deps": SophieAIToolContenxt(connection=connection)}
    async with track_ai_conversation():
        result = await new_ai_generate(
            history,
            tools=CHATBOT_TOOLS,
            model=model,
            agent_kwargs=agent_kwargs,
            user_tracking_id=chat.iid,
            session_id=f"{chat.iid}:{target_message.message_thread_id or 'proactive'}",
            service_tier=service_tier,
        )
    if result.usage:
        track_ai_usage(model, result.usage)
        await charge_ai_usage(chat.iid, AI_FEATURE_CHATBOT, model, result.usage)
    header = await _build_chatbot_header(connection, model, result.message_history)
    output_text = _truncate_output(header, str(result.output))
    doc = await _build_reply_doc(header, output_text, model, result, False, chat_tid=chat_tid)
    try:
        sent_message = await bot.send_message(
            chat_id=chat_tid,
            text=doc.to_html(),
            disable_web_page_preview=True,
            reply_to_message_id=target_message.message_id,
            message_thread_id=target_message.message_thread_id,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        log.debug("Proactive AI answer skipped", chat_id=chat_tid, message_id=target_message.message_id, error=error)
        return
    await cache_message(
        sent_message.text,
        chat_tid,
        CONFIG.bot_id,
        sent_message.message_id,
        sent_message.date,
        "Sophie",
        message_thread_id=sent_message.message_thread_id,
        handled_by_ai=True,
        eligible_for_proactive_ai=False,
        proactively_answered=True,
    )


async def _execute_actions(
    chat_tid: int,
    chat: ChatModel,
    messages: tuple[MessageType, ...],
    decision: ProactiveDecision,
    settings: ProactiveReplySettings,
) -> None:
    messages_by_id = _target_by_message_id(messages)
    for action in _limit_actions(decision, settings):
        if action.message_id is None or action.message_id not in messages_by_id:
            continue
        target_message = messages_by_id[action.message_id]
        if action.action == "react":
            await _react_to_message(chat_tid, target_message, action.emoji)
        if action.action == "answer":
            await _answer_message(chat_tid, chat, target_message)


async def maybe_run_proactive_reply(message: Message, chat: ChatModel) -> None:
    chat_tid = chat.tid
    if not await is_enabled("ai_proactive_replies", chat_tid=chat_tid):
        return
    if message.chat.type not in {"group", "supergroup"}:
        return
    quota_result = await check_quota(chat.iid)
    if not quota_result.allowed:
        return

    settings = await _get_settings(chat_tid)
    tracked_count = await _track_eligible_message(chat_tid, message, settings)
    if tracked_count < settings.min_messages:
        return
    if not await _acquire_lock(chat_tid):
        return

    try:
        candidates = await _get_recent_candidates(chat_tid, settings)
        if len(candidates) < settings.min_messages:
            return
        try:
            decision = await _generate_decision(chat, chat_tid, candidates, settings)
            await _execute_actions(chat_tid, chat, candidates, decision, settings)
            await _clear_tracked_messages(chat_tid, candidates)
        except SophieException as error:
            log.debug("Proactive AI decision skipped", chat_id=chat_tid, error=error)
            await _clear_tracked_messages(chat_tid, candidates)
    finally:
        await _release_lock(chat_tid)
