from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from pydantic import BaseModel

from sophie_bot.utils.feature_flags import FeatureType, get_service_tier, get_value

_DEFAULT_PROACTIVE_BATCH_SIZE: Final[int] = 30
_DEFAULT_PROACTIVE_WINDOW_SECONDS: Final[int] = 180
_DEFAULT_PROACTIVE_MAX_ANSWERS: Final[int] = 1
_DEFAULT_PROACTIVE_MAX_REACTIONS: Final[int] = 1
_DEFAULT_PROACTIVE_MIN_MESSAGES: Final[int] = 12
_MAX_PROACTIVE_DECISION_ANSWERS: Final[int] = 1
_MAX_PROACTIVE_DECISION_REACTIONS: Final[int] = 2
_DEFAULT_PROACTIVE_PROMPT: Final[str] = (
    "Use balanced judgment about whether Sophie should join the conversation. Reply when there is a natural, useful, "
    "or funny opportunity, including a clear invitation or an open question Sophie can help with. Do not force a "
    "reply: skip generic chatter, arguments, moderation/admin topics, stale topics, or messages that have already "
    "moved on. Never duplicate an existing AI reply, bypass safety requirements, or answer unsafe requests. Prefer "
    "no action when a reply would be awkward or mediocre. If answering, be brief: 1-2 short sentences, casual, no "
    "long explanations or lists unless explicitly needed. React only when the reaction is obviously appropriate and "
    "lightweight, and never try to participate in every topic."
)

_DEFAULT_RESEARCH_MAX_ROUNDS: Final[int] = 3
_DEFAULT_RESEARCH_QUERIES_PER_ROUND: Final[int] = 5
_DEFAULT_RESEARCH_RESULTS_PER_QUERY: Final[int] = 5


class ProactiveReplySettings(BaseModel):
    batch_size: int = _DEFAULT_PROACTIVE_BATCH_SIZE
    window_seconds: int = _DEFAULT_PROACTIVE_WINDOW_SECONDS
    max_answers: int = _DEFAULT_PROACTIVE_MAX_ANSWERS
    max_reactions: int = _DEFAULT_PROACTIVE_MAX_REACTIONS
    min_messages: int = _DEFAULT_PROACTIVE_MIN_MESSAGES
    prompt: str = _DEFAULT_PROACTIVE_PROMPT


@dataclass(frozen=True)
class ResearchWorkflowSettings:
    max_rounds: int
    queries_per_round: int
    results_per_query: int
    service_tier: str | None


def coerce_positive_int(value: object, default: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        parsed_value = int(value)
    except ValueError:
        return default
    return default if parsed_value <= 0 else min(parsed_value, maximum)


async def _feature_int(feature: FeatureType, chat_tid: int, default: int, minimum: int = 1) -> int:
    value = await get_value(feature, chat_tid=chat_tid)
    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed_value, minimum)


async def get_proactive_reply_settings(chat_tid: int) -> ProactiveReplySettings:
    batch_size = await _feature_int("ai_proactive_replies_batch_size", chat_tid, _DEFAULT_PROACTIVE_BATCH_SIZE)
    window_seconds = await _feature_int(
        "ai_proactive_replies_window_seconds", chat_tid, _DEFAULT_PROACTIVE_WINDOW_SECONDS
    )
    max_answers = await _feature_int(
        "ai_proactive_replies_max_answers", chat_tid, _DEFAULT_PROACTIVE_MAX_ANSWERS, minimum=0
    )
    max_reactions = await _feature_int(
        "ai_proactive_replies_max_reactions", chat_tid, _DEFAULT_PROACTIVE_MAX_REACTIONS, minimum=0
    )
    min_messages = await _feature_int("ai_proactive_replies_min_messages", chat_tid, _DEFAULT_PROACTIVE_MIN_MESSAGES)
    prompt = str(await get_value("ai_proactive_replies_prompt", chat_tid=chat_tid))
    return ProactiveReplySettings(
        batch_size=batch_size,
        window_seconds=window_seconds,
        max_answers=min(max_answers, _MAX_PROACTIVE_DECISION_ANSWERS),
        max_reactions=min(max_reactions, _MAX_PROACTIVE_DECISION_REACTIONS),
        min_messages=min(min_messages, batch_size),
        prompt=prompt,
    )


async def _coerce_feature_int(feature: FeatureType, chat_tid: int | None, default: int, maximum: int) -> int:
    return coerce_positive_int(await get_value(feature, chat_tid=chat_tid), default, maximum)


async def get_research_workflow_settings(chat_tid: int | None = None) -> ResearchWorkflowSettings:
    return ResearchWorkflowSettings(
        max_rounds=await _coerce_feature_int("ai_research_max_rounds", chat_tid, _DEFAULT_RESEARCH_MAX_ROUNDS, 5),
        queries_per_round=await _coerce_feature_int(
            "ai_research_queries_per_round", chat_tid, _DEFAULT_RESEARCH_QUERIES_PER_ROUND, 10
        ),
        results_per_query=await _coerce_feature_int(
            "ai_research_results_per_query", chat_tid, _DEFAULT_RESEARCH_RESULTS_PER_QUERY, 10
        ),
        service_tier=await get_service_tier(cast(FeatureType, "ai_research_service_tier"), chat_tid=chat_tid),
    )
