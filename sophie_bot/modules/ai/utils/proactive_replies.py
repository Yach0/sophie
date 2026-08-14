from __future__ import annotations

from typing import Literal, cast

from aiogram.types import Message, ReactionTypeEmoji
from pydantic import BaseModel, Field
from pydantic_ai.messages import UserContent
from sentry_sdk.ai import set_conversation_id
from stfu_tg import Doc

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.metrics import (
    track_ai_conversation,
    track_ai_proactive_action,
    track_ai_proactive_batch,
    track_ai_proactive_event,
)
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_default_model_plan
from sophie_bot.modules.ai.utils.ai_models import get_proactive_replies_model_plan
from sophie_bot.modules.ai.utils.ai_quota import check_quota
from sophie_bot.modules.ai.utils.ai_run import run_ai_text
from sophie_bot.modules.ai.utils.ai_tasks import AIStructuredTask, run_structured_task
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.cache_messages import MessageType, cache_message
from sophie_bot.modules.ai.utils.chatbot_agent import build_chatbot_run_config
from sophie_bot.modules.ai.utils.chatbot_response import build_chatbot_header, build_reply_doc, truncate_output
from sophie_bot.modules.ai.utils.feature_settings import ProactiveReplySettings, get_proactive_reply_settings
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory, AIUserMessageFormatter
from sophie_bot.modules.ai.utils.proactive_prompt import build_decision_history as _build_decision_history
from sophie_bot.modules.ai.utils.proactive_tracking import (
    acquire_lock as _acquire_lock,
)
from sophie_bot.modules.ai.utils.proactive_tracking import (
    clear_tracked_messages as _clear_tracked_messages,
)
from sophie_bot.modules.ai.utils.proactive_tracking import (
    get_recent_candidates as _get_recent_candidates,
)
from sophie_bot.modules.ai.utils.proactive_tracking import (
    log_proactive_info as _log_proactive_info,
)
from sophie_bot.modules.ai.utils.proactive_tracking import (
    release_lock as _release_lock,
)
from sophie_bot.modules.ai.utils.proactive_tracking import (
    track_eligible_message as _track_eligible_message,
)
from sophie_bot.services.bot import bot
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_service_tier, is_enabled
from sophie_bot.utils.i18n import gettext as _

ProactiveActionName = Literal["none", "react", "answer"]

_TELEGRAM_REACTION_EMOJIS: frozenset[str] = frozenset(
    {
        "❤",
        "👍",
        "👎",
        "🔥",
        "🥰",
        "👏",
        "😁",
        "🤔",
        "🤯",
        "😱",
        "🤬",
        "😢",
        "🎉",
        "🤩",
        "🤮",
        "💩",
        "🙏",
        "👌",
        "🕊",
        "🤡",
        "🥱",
        "🥴",
        "😍",
        "🐳",
        "❤‍🔥",
        "🌚",
        "🌭",
        "💯",
        "🤣",
        "⚡",
        "🍌",
        "🏆",
        "💔",
        "🤨",
        "😐",
        "🍓",
        "🍾",
        "💋",
        "🖕",
        "😈",
        "😴",
        "😭",
        "🤓",
        "👻",
        "👨‍💻",
        "👀",
        "🎃",
        "🙈",
        "😇",
        "😨",
        "🤝",
        "✍",
        "🤗",
        "🫡",
        "🎅",
        "🎄",
        "☃",
        "💅",
        "🤪",
        "🗿",
        "🆒",
        "💘",
        "🙉",
        "🦄",
        "😘",
        "💊",
        "🙊",
        "😎",
        "👾",
        "🤷‍♂",
        "🤷",
        "🤷‍♀",
        "😡",
    }
)
_FALLBACK_REACTION_EMOJI = "👍"


class ProactiveAction(BaseModel):
    action: ProactiveActionName
    message_id: int | None = None
    emoji: str | None = None
    reason: str | None = None


class ProactiveDecision(BaseModel):
    actions: list[ProactiveAction] = Field(default_factory=list)


async def _get_settings(chat_tid: int) -> ProactiveReplySettings:
    settings = await get_proactive_reply_settings(chat_tid)
    _log_proactive_info(
        "Proactive AI settings resolved",
        chat_id=chat_tid,
        batch_size=settings.batch_size,
        window_seconds=settings.window_seconds,
        max_answers=settings.max_answers,
        max_reactions=settings.max_reactions,
        min_messages=settings.min_messages,
        prompt_length=len(settings.prompt),
    )
    return settings


_METRIC_ATTRIBUTES: dict[str, str] = {"feature": "ai_proactive_replies"}


def _target_by_message_id(messages: tuple[MessageType, ...]) -> dict[int, MessageType]:
    return {message.message_id: message for message in messages}


def _normalize_reaction_emoji(emoji: str | None) -> str | None:
    if not emoji:
        return None
    stripped_emoji = emoji.strip()
    if stripped_emoji in _TELEGRAM_REACTION_EMOJIS:
        return stripped_emoji
    _log_proactive_info(
        "Proactive AI reaction emoji normalized",
        requested_emoji=stripped_emoji,
        fallback_emoji=_FALLBACK_REACTION_EMOJI,
    )
    return _FALLBACK_REACTION_EMOJI


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


async def _generate_decision(
    chat: ChatModel, chat_tid: int, messages: tuple[MessageType, ...], settings: ProactiveReplySettings
) -> ProactiveDecision:
    model_plan = await get_proactive_replies_model_plan(chat_tid)
    service_tier = await get_service_tier("ai_proactive_replies_service_tier", chat_tid=chat_tid)
    _log_proactive_info(
        "Proactive AI decision request started",
        chat_id=chat_tid,
        model=model_plan.primary.model_name,
        service_tier=service_tier or "none",
        message_count=len(messages),
    )
    history = _build_decision_history(messages, settings)
    result = await run_structured_task(
        AIStructuredTask(
            output_type=ProactiveDecision,
            feature=AI_FEATURE_CHATBOT,
        ),
        model_plan,
        history,
        chat_iid=chat.iid,
        chat_tid=chat_tid,
        session_id=f"proactive:{chat.iid}",
        service_tier=service_tier,
    )
    limited_actions = _limit_actions(result.output, settings)
    track_ai_proactive_event("decision_generated", _METRIC_ATTRIBUTES)
    track_ai_proactive_batch(len(messages), len(limited_actions), _METRIC_ATTRIBUTES)
    if not limited_actions:
        track_ai_proactive_action("none", _METRIC_ATTRIBUTES)
    for action in limited_actions:
        track_ai_proactive_action(action.action, _METRIC_ATTRIBUTES)
    _log_proactive_info(
        "Proactive AI decision generated",
        chat_id=chat_tid,
        action_count=len(limited_actions),
        raw_action_count=len(result.output.actions),
    )
    return result.output


async def _react_to_message(chat_tid: int, target_message: MessageType, emoji: str | None) -> None:
    reaction_emoji = _normalize_reaction_emoji(emoji)
    if not reaction_emoji:
        _log_proactive_info(
            "Proactive AI reaction skipped without emoji", chat_id=chat_tid, message_id=target_message.message_id
        )
        return
    _log_proactive_info(
        "Proactive AI reaction send started",
        chat_id=chat_tid,
        message_id=target_message.message_id,
        emoji=reaction_emoji,
    )
    await bot.set_message_reaction(
        chat_id=chat_tid,
        message_id=target_message.message_id,
        reaction=[ReactionTypeEmoji(emoji=reaction_emoji)],
    )
    track_ai_proactive_event("reaction_sent", _METRIC_ATTRIBUTES)
    _log_proactive_info(
        "Proactive AI reaction sent",
        chat_id=chat_tid,
        message_id=target_message.message_id,
        emoji=reaction_emoji,
    )


async def _build_answer_history(chat_tid: int, target_message: MessageType) -> AIMessageHistory:
    history = AIMessageHistory()
    proactive_answer_prompt = Doc(
        _(
            "You are proactively joining a Telegram group chat. Keep the reply timely, casual, and very short: "
            "1-2 short sentences. Do not include long explanations, bullet lists, or tool-like detail unless the "
            "target message explicitly asks for it. If the topic has moved on or a reply would feel forced, keep the "
            "answer minimal instead of trying to cover everything."
        ),
    )
    history.add_system(proactive_answer_prompt.to_md())
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
    model_plan = await get_chat_default_model_plan(chat.iid, chat_tid=chat_tid)
    model = model_plan.primary
    service_tier = await get_service_tier("ai_chatbot_service_tier", chat_tid=chat_tid)
    _log_proactive_info(
        "Proactive AI answer generation started",
        chat_id=chat_tid,
        message_id=target_message.message_id,
        model=model.model_name,
        service_tier=service_tier or "none",
    )
    history = await _build_answer_history(chat_tid, target_message)
    run_config = await build_chatbot_run_config(
        chat_tid,
        connection,
        model,
        user_text=target_message.text,
        user_tid=None,
        thread_id=target_message.message_thread_id,
        session_id=f"{chat.iid}:{target_message.message_thread_id or 'proactive'}",
        service_tier=service_tier,
        use_base_tools=True,
    )
    async with track_ai_conversation():
        set_conversation_id(f"{chat.iid}:proactive")
        result = await run_ai_text(
            run_config.agent,
            user_prompt=history.prompt,
            message_history=history.message_history,
            deps=run_config.deps,
            usage_limits=run_config.usage_limits,
            request_options=run_config.request_options,
            model_plan=model_plan,
        )
    # Failover may have moved the answer to another candidate; bill and label the one that served it.
    model = result.served_model or model
    if result.usage:
        await charge_ai_usage(chat.iid, AI_FEATURE_CHATBOT, model, result.usage)
    header = await build_chatbot_header(chat.iid, model, result.message_history)
    output_text = truncate_output(header, str(result.output))
    doc = await build_reply_doc(header, output_text, model, result, False, chat_tid=chat_tid)
    _log_proactive_info(
        "Proactive AI answer send started",
        chat_id=chat_tid,
        message_id=target_message.message_id,
        output_length=len(doc.to_html()),
    )
    sent_message = await bot.send_message(
        chat_id=chat_tid,
        text=doc.to_html(),
        disable_web_page_preview=True,
        reply_to_message_id=target_message.message_id,
        message_thread_id=target_message.message_thread_id,
    )
    track_ai_proactive_event("answer_sent", _METRIC_ATTRIBUTES)
    _log_proactive_info(
        "Proactive AI answer sent",
        chat_id=chat_tid,
        message_id=target_message.message_id,
        sent_message_id=sent_message.message_id,
    )
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
    limited_actions = _limit_actions(decision, settings)
    _log_proactive_info("Proactive AI executing actions", chat_id=chat_tid, action_count=len(limited_actions))
    for action in limited_actions:
        if action.message_id is None or action.message_id not in messages_by_id:
            track_ai_proactive_event("action_invalid_target", _METRIC_ATTRIBUTES)
            _log_proactive_info(
                "Proactive AI action skipped due to invalid target",
                chat_id=chat_tid,
                action=action.action,
                message_id=action.message_id,
            )
            continue
        target_message = messages_by_id[action.message_id]
        if action.action == "react":
            track_ai_proactive_event("action_react_selected", _METRIC_ATTRIBUTES)
            _log_proactive_info(
                "Proactive AI reaction action selected",
                chat_id=chat_tid,
                message_id=target_message.message_id,
                emoji=action.emoji,
            )
            await _react_to_message(chat_tid, target_message, action.emoji)
        if action.action == "answer":
            track_ai_proactive_event("action_answer_selected", _METRIC_ATTRIBUTES)
            _log_proactive_info(
                "Proactive AI answer action selected",
                chat_id=chat_tid,
                message_id=target_message.message_id,
            )
            await _answer_message(chat_tid, chat, target_message)


async def maybe_run_proactive_reply(message: Message, chat: ChatModel) -> None:
    chat_tid = chat.tid
    if not await is_enabled("ai_proactive_replies", chat_tid=chat_tid):
        return
    if message.chat.type not in {"group", "supergroup"}:
        _log_proactive_info("Proactive AI skipped outside group chat", chat_id=chat_tid, chat_type=message.chat.type)
        return
    _log_proactive_info("Proactive AI evaluation started", chat_id=chat_tid, message_id=message.message_id)
    quota_result = await check_quota(chat.iid)
    if not quota_result.allowed:
        track_ai_proactive_event("quota_exhausted", _METRIC_ATTRIBUTES)
        _log_proactive_info("Proactive AI skipped because quota is exhausted", chat_id=chat_tid)
        return

    settings = await _get_settings(chat_tid)
    tracked_count = await _track_eligible_message(chat_tid, message, settings)
    track_ai_proactive_event("eligible_message", _METRIC_ATTRIBUTES)
    if tracked_count < settings.min_messages:
        track_ai_proactive_event("below_threshold", _METRIC_ATTRIBUTES)
        _log_proactive_info(
            "Proactive AI waiting for more messages",
            chat_id=chat_tid,
            tracked_count=tracked_count,
            min_messages=settings.min_messages,
        )
        return
    if not await _acquire_lock(chat_tid):
        track_ai_proactive_event("lock_busy", _METRIC_ATTRIBUTES)
        _log_proactive_info("Proactive AI skipped because lock is busy", chat_id=chat_tid)
        return

    try:
        candidates = await _get_recent_candidates(chat_tid, settings)
        if len(candidates) < settings.min_messages:
            track_ai_proactive_event("no_candidates", _METRIC_ATTRIBUTES)
            _log_proactive_info(
                "Proactive AI skipped because candidate count is below minimum",
                chat_id=chat_tid,
                candidate_count=len(candidates),
                min_messages=settings.min_messages,
            )
            return
        track_ai_proactive_event("batch_started", _METRIC_ATTRIBUTES)
        _log_proactive_info(
            "Proactive AI batch started",
            chat_id=chat_tid,
            candidate_count=len(candidates),
        )
        decision = await _generate_decision(chat, chat_tid, candidates, settings)
        await _execute_actions(chat_tid, chat, candidates, decision, settings)
        await _clear_tracked_messages(chat_tid, candidates)
        _log_proactive_info("Proactive AI batch completed", chat_id=chat_tid, candidate_count=len(candidates))
    finally:
        await _release_lock(chat_tid)
