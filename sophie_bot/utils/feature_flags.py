from __future__ import annotations

from typing import Any, Final, Literal, TypedDict, cast

from sentry_sdk import feature_flags as sentry_feature_flags

from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.services.redis import aredis

# Public types
FeatureType = Literal[
    "ai_summary_model",
    "ai_filter_handler_model",
    "ai_chatbot_model",
    "ai_translation_model",
    "ai_search_provider",
    "ai_chatbot_system_prompt",
    "ai_translate_system_prompt",
    "ai_chat_summaries_prompt",
    "ai_moderation_reason_prompt",
    "ai_filter_suggestions_prompt",
    "ai_chatbot",
    "ai_chatbot_admin_status",
    "ai_chatbot_chat_name",
    "ai_chatbot_blockquote",
    "ai_chatbot_research_quote",
    "ai_chatbot_thinking_message",
    "ai_chatbot_tool_thinking",
    "ai_chatbot_random_emoji",
    "ai_chatbot_streaming",
    "ai_chatbot_streaming_backoff_seconds",
    "ai_chatbot_request_limit",
    "ai_chatbot_tool_calls_limit",
    "ai_chatbot_response_tokens_limit",
    "ai_translations",
    "ai_moderation",
    "ai_moderation_reasons",
    "ai_filters",
    "ai_chat_summaries",
    "ai_system_prompt_summaries",
    "ai_notes_related_system_prompt",
    "ai_notes_related_system_prompt_full_content",
    "ai_agent_save_notes",
    "ai_memories_to_notes",
    "ai_delete_notes",
    "notes_rag_embeddings",
    "notes_rag_search_command",
    "notes_rag_list_search",
    "filters",
    "antiflood",
    "locks",
    "welcomecaptcha",
    "welcomecaptcha_autokick",
    "op_debug_ai_summarization",
    "ai_chatbot_service_tier",
    "ai_translations_service_tier",
    "ai_filters_service_tier",
    "ai_chat_summaries_service_tier",
    "ai_proactive_replies",
    "ai_proactive_replies_model",
    "ai_proactive_replies_prompt",
    "ai_proactive_replies_service_tier",
    "ai_proactive_replies_batch_size",
    "ai_proactive_replies_window_seconds",
    "ai_proactive_replies_max_answers",
    "ai_proactive_replies_max_reactions",
    "ai_proactive_replies_min_messages",
    "ai_research",
    "ai_research_model",
    "ai_research_max_rounds",
    "ai_research_queries_per_round",
    "ai_research_results_per_query",
    "ai_research_service_tier",
]


class FeatureStates(TypedDict):
    ai_summary_model: str
    ai_filter_handler_model: str
    ai_chatbot_model: str
    ai_translation_model: str
    ai_search_provider: str
    ai_chatbot_system_prompt: str
    ai_translate_system_prompt: str
    ai_chat_summaries_prompt: str
    ai_moderation_reason_prompt: str
    ai_filter_suggestions_prompt: str
    ai_chatbot: bool
    ai_chatbot_admin_status: bool
    ai_chatbot_chat_name: bool
    ai_chatbot_blockquote: bool
    ai_chatbot_research_quote: bool
    ai_chatbot_thinking_message: bool
    ai_chatbot_tool_thinking: bool
    ai_chatbot_random_emoji: bool
    ai_chatbot_streaming: bool
    ai_chatbot_streaming_backoff_seconds: float
    ai_chatbot_request_limit: int
    ai_chatbot_tool_calls_limit: int
    ai_chatbot_response_tokens_limit: int
    ai_translations: bool
    ai_moderation: bool
    ai_moderation_reasons: bool
    ai_filters: bool
    ai_chat_summaries: bool
    ai_system_prompt_summaries: bool
    ai_notes_related_system_prompt: bool
    ai_notes_related_system_prompt_full_content: bool
    ai_agent_save_notes: bool
    ai_memories_to_notes: bool
    ai_delete_notes: bool
    notes_rag_embeddings: bool
    notes_rag_search_command: bool
    notes_rag_list_search: bool
    filters: bool
    antiflood: bool
    locks: bool
    welcomecaptcha: bool
    welcomecaptcha_autokick: bool
    op_debug_ai_summarization: bool
    ai_chatbot_service_tier: str
    ai_translations_service_tier: str
    ai_filters_service_tier: str
    ai_chat_summaries_service_tier: str
    ai_proactive_replies: bool
    ai_proactive_replies_model: str
    ai_proactive_replies_prompt: str
    ai_proactive_replies_service_tier: str
    ai_proactive_replies_batch_size: int
    ai_proactive_replies_window_seconds: int
    ai_proactive_replies_max_answers: int
    ai_proactive_replies_max_reactions: int
    ai_proactive_replies_min_messages: int
    ai_research: bool
    ai_research_model: str
    ai_research_max_rounds: int
    ai_research_queries_per_round: int
    ai_research_results_per_query: int
    ai_research_service_tier: str


FEATURE_FLAGS: Final[tuple[FeatureType, ...]] = (
    "ai_summary_model",
    "ai_filter_handler_model",
    "ai_chatbot_model",
    "ai_translation_model",
    "ai_search_provider",
    "ai_chatbot_system_prompt",
    "ai_translate_system_prompt",
    "ai_chat_summaries_prompt",
    "ai_moderation_reason_prompt",
    "ai_filter_suggestions_prompt",
    "ai_chatbot",
    "ai_chatbot_admin_status",
    "ai_chatbot_chat_name",
    "ai_chatbot_blockquote",
    "ai_chatbot_research_quote",
    "ai_chatbot_thinking_message",
    "ai_chatbot_tool_thinking",
    "ai_chatbot_random_emoji",
    "ai_chatbot_streaming",
    "ai_chatbot_streaming_backoff_seconds",
    "ai_chatbot_request_limit",
    "ai_chatbot_tool_calls_limit",
    "ai_chatbot_response_tokens_limit",
    "ai_translations",
    "ai_moderation",
    "ai_moderation_reasons",
    "ai_filters",
    "ai_chat_summaries",
    "ai_system_prompt_summaries",
    "ai_notes_related_system_prompt",
    "ai_notes_related_system_prompt_full_content",
    "ai_agent_save_notes",
    "ai_memories_to_notes",
    "ai_delete_notes",
    "notes_rag_embeddings",
    "notes_rag_search_command",
    "notes_rag_list_search",
    "filters",
    "antiflood",
    "locks",
    "welcomecaptcha",
    "welcomecaptcha_autokick",
    "op_debug_ai_summarization",
    "ai_chatbot_service_tier",
    "ai_translations_service_tier",
    "ai_filters_service_tier",
    "ai_chat_summaries_service_tier",
    "ai_proactive_replies",
    "ai_proactive_replies_model",
    "ai_proactive_replies_prompt",
    "ai_proactive_replies_service_tier",
    "ai_proactive_replies_batch_size",
    "ai_proactive_replies_window_seconds",
    "ai_proactive_replies_max_answers",
    "ai_proactive_replies_max_reactions",
    "ai_proactive_replies_min_messages",
    "ai_research",
    "ai_research_model",
    "ai_research_max_rounds",
    "ai_research_queries_per_round",
    "ai_research_results_per_query",
    "ai_research_service_tier",
)


def _default_state_map() -> dict[FeatureType, FeatureValue]:
    return _DEFAULT_STATES.copy()


FeatureValue = bool | str | int | float


_DEFAULT_STATES: Final[dict[FeatureType, FeatureValue]] = {
    "ai_summary_model": "openai/gpt-5.5",
    "ai_filter_handler_model": "openai/gpt-5-nano",
    "ai_chatbot_model": "",
    "ai_translation_model": "",
    "ai_search_provider": "kagi",
    "ai_chatbot_system_prompt": "You're a telegram bot named Sophie.\nBe funny when the topic is casual.\nSend short messages unless longer explanations are needed.\nDo not reply to many messages at once, focus on the latest message only.\nPrefer to search information in the internet",
    "ai_translate_system_prompt": "You're a professional AI translator / transcriber.\nSet translation_explanations to null unless the source is ambiguous, self-contradictory, requires culturally/contextually essential explanation, contains untranslatable idiom/wordplay/polysemy affecting meaning, or needs disambiguation of a proper noun/technical term/abbreviation;if included, keep it concise (≤2 factual sentences).",
    "ai_chat_summaries_prompt": "Summarize the chat day into one short general overview and several topic lines.\nEach topic line must contain a short title, one fitting emoji, and the list of source message IDs.\nDo not include any IDs that are not present in the provided transcript.\nSkip one-off chatter that does not form a meaningful discussion.\nPrefer topics that include at least three messages or at least two participants, and avoid weak one-person fragments.",
    "ai_moderation_reason_prompt": "Generate a brief, professional moderation reason for restricting a user based on their message.",
    "ai_filter_suggestions_prompt": "You generate Sophie Bot filter handler suggestions.\nReturn 1 to 3 unique suggestions as structured data.",
    "ai_chatbot": True,
    "ai_chatbot_admin_status": False,
    "ai_chatbot_chat_name": False,
    "ai_chatbot_blockquote": True,
    "ai_chatbot_research_quote": True,
    "ai_chatbot_thinking_message": False,
    "ai_chatbot_tool_thinking": False,
    "ai_chatbot_random_emoji": False,
    "ai_chatbot_streaming": False,
    "ai_chatbot_streaming_backoff_seconds": 1.5,
    "ai_chatbot_request_limit": 8,
    "ai_chatbot_tool_calls_limit": 12,
    "ai_chatbot_response_tokens_limit": 0,
    "ai_translations": True,
    "ai_moderation": True,
    "ai_moderation_reasons": True,
    "ai_filters": True,
    "ai_chat_summaries": True,
    "ai_system_prompt_summaries": False,
    "ai_notes_related_system_prompt": False,
    "ai_notes_related_system_prompt_full_content": False,
    "ai_agent_save_notes": False,
    "ai_memories_to_notes": False,
    "ai_delete_notes": False,
    "notes_rag_embeddings": False,
    "notes_rag_search_command": False,
    "notes_rag_list_search": False,
    "filters": True,
    "antiflood": True,
    "locks": True,
    "welcomecaptcha": True,
    "welcomecaptcha_autokick": True,
    "op_debug_ai_summarization": False,
    "ai_chatbot_service_tier": "none",
    "ai_translations_service_tier": "none",
    "ai_filters_service_tier": "none",
    "ai_chat_summaries_service_tier": "none",
    "ai_proactive_replies": False,
    "ai_proactive_replies_model": "openai/gpt-5-nano",
    "ai_proactive_replies_prompt": "Be very conservative. Most batches should result in no action. Only answer if Sophie was clearly invited into the conversation, someone asks an open question that Sophie can help with, or there is a very strong natural opportunity for a short useful/funny reply. Do not answer generic chatter, small talk, arguments, moderation/admin topics, old topics, or messages that already moved on. Prefer no action over a mediocre answer. If answering, be brief: 1-2 short sentences, casual, no long explanations, no lists unless explicitly needed. React only when the reaction is obviously appropriate and lightweight. Never try to participate in every topic.",
    "ai_proactive_replies_service_tier": "flex",
    "ai_proactive_replies_batch_size": 30,
    "ai_proactive_replies_window_seconds": 180,
    "ai_proactive_replies_max_answers": 1,
    "ai_proactive_replies_max_reactions": 1,
    "ai_proactive_replies_min_messages": 30,
    "ai_research": False,
    "ai_research_model": "openai/gpt-5.5",
    "ai_research_max_rounds": 3,
    "ai_research_queries_per_round": 5,
    "ai_research_results_per_query": 5,
    "ai_research_service_tier": "flex",
}


_REDIS_KEY: Final[str] = "sophie:kill_switch"
_REDIS_CHAT_KEY_PREFIX: Final[str] = "sophie:kill_switch_chat"
_TRUE_VALUE: Final[str] = "1"
_FALSE_VALUE: Final[str] = "0"


def _chat_redis_key(chat_tid: int) -> str:
    return f"{_REDIS_CHAT_KEY_PREFIX}:{chat_tid}"


def _serialize_value(value: FeatureValue) -> str:
    if isinstance(value, bool):
        return _TRUE_VALUE if value else _FALSE_VALUE
    return str(value)


def _parse_override(value: bytes | str | None, default: FeatureValue) -> FeatureValue | None:
    if value is None:
        return None
    normalized_value = value.decode() if isinstance(value, bytes) else value
    if normalized_value.lower() in {"true", _TRUE_VALUE}:
        return True
    if normalized_value.lower() in {"false", _FALSE_VALUE}:
        return False
    try:
        return int(normalized_value)
    except ValueError:
        pass
    try:
        return float(normalized_value)
    except ValueError:
        return normalized_value if not isinstance(default, bool) else None


def _coerce_db_value(value: Any) -> FeatureValue | None:
    if isinstance(value, bool | str | int | float):
        return value
    return None


async def _get_override(feature: FeatureType) -> FeatureValue | None:
    value = await aredis.hget(_REDIS_KEY, feature)  # ty: ignore[invalid-await]
    parsed_value = _parse_override(value, _DEFAULT_STATES[feature])
    if parsed_value is not None:
        return parsed_value

    override = await FeatureFlagOverride.get_override(feature)
    if override is None:
        return None

    db_value = _coerce_db_value(override.value)
    if db_value is not None:
        await aredis.hset(_REDIS_KEY, feature, _serialize_value(db_value))  # ty: ignore[invalid-await]
    return db_value


async def _set_override(feature: FeatureType, value: FeatureValue) -> None:
    await FeatureFlagOverride.set_override(feature, value)
    await aredis.hset(_REDIS_KEY, feature, _serialize_value(value))  # ty: ignore[invalid-await]


async def _get_all_overrides() -> dict[FeatureType, FeatureValue]:
    parsed_overrides: dict[FeatureType, FeatureValue] = {}

    async for override in FeatureFlagOverride.find({"chat_tid": None}):  # deepsource-ignore[PYL-E1133]
        if override.feature not in FEATURE_FLAGS:
            continue

        typed_feature = cast(FeatureType, override.feature)
        parsed_value = _coerce_db_value(override.value)
        if parsed_value is None:
            continue

        parsed_overrides[typed_feature] = parsed_value
        await aredis.hset(_REDIS_KEY, typed_feature, _serialize_value(parsed_value))  # ty: ignore[invalid-await]

    return parsed_overrides


def _track_feature_in_sentry(feature: FeatureType, enabled: bool) -> None:
    sentry_feature_flags.add_feature_flag(feature, enabled)


async def get_chat_override(feature: FeatureType, chat_tid: int) -> FeatureValue | None:
    value = await aredis.hget(_chat_redis_key(chat_tid), feature)  # ty: ignore[invalid-await]
    parsed_value = _parse_override(value, _DEFAULT_STATES[feature])
    if parsed_value is not None:
        return parsed_value

    override = await FeatureFlagOverride.get_override(feature, chat_tid=chat_tid)
    if override is None:
        return None

    db_value = _coerce_db_value(override.value)
    if db_value is not None:
        await aredis.hset(_chat_redis_key(chat_tid), feature, _serialize_value(db_value))  # ty: ignore[invalid-await]
    return db_value


async def set_chat_override(feature: FeatureType, chat_tid: int, value: FeatureValue) -> None:
    await FeatureFlagOverride.set_override(feature, value, chat_tid=chat_tid)
    await aredis.hset(_chat_redis_key(chat_tid), feature, _serialize_value(value))  # ty: ignore[invalid-await]


async def delete_override(feature: FeatureType) -> None:
    await FeatureFlagOverride.delete_override(feature)
    await aredis.hdel(_REDIS_KEY, feature)  # ty: ignore[invalid-await]


async def delete_chat_override(feature: FeatureType, chat_tid: int) -> None:
    await FeatureFlagOverride.delete_override(feature, chat_tid=chat_tid)
    await aredis.hdel(_chat_redis_key(chat_tid), feature)  # ty: ignore[invalid-await]


async def list_chat_overrides(chat_tid: int) -> dict[FeatureType, FeatureValue]:
    parsed_overrides: dict[FeatureType, FeatureValue] = {}
    async for override in FeatureFlagOverride.find(
        FeatureFlagOverride.chat_tid == chat_tid
    ):  # deepsource-ignore[PYL-E1133]
        if override.feature not in FEATURE_FLAGS:
            continue
        typed_feature = cast(FeatureType, override.feature)
        parsed_value = _coerce_db_value(override.value)
        if parsed_value is not None:
            parsed_overrides[typed_feature] = parsed_value
            await aredis.hset(_chat_redis_key(chat_tid), typed_feature, _serialize_value(parsed_value))  # ty: ignore[invalid-await]
    return parsed_overrides


async def get_value(feature: FeatureType, chat_tid: int | None = None) -> FeatureValue:
    if chat_tid is not None:
        chat_override = await get_chat_override(feature, chat_tid)
        if chat_override is not None:
            return chat_override
    override = await _get_override(feature)
    return override if override is not None else _DEFAULT_STATES[feature]


async def set_value(feature: FeatureType, value: FeatureValue) -> None:
    await _set_override(feature, value)


async def is_enabled(feature: FeatureType, chat_tid: int | None = None) -> bool:
    enabled = bool(await get_value(feature, chat_tid=chat_tid))
    _track_feature_in_sentry(feature, enabled)
    return enabled


async def set_enabled(feature: FeatureType, enabled: bool) -> None:
    await set_value(feature, enabled)
    _track_feature_in_sentry(feature, enabled)


async def get_service_tier(feature: FeatureType, chat_tid: int | None = None) -> str | None:
    """Return the service_tier value from a feature flag, or None if set to \"none\"."""
    value = str(await get_value(feature, chat_tid=chat_tid))
    if value == "none":
        return None
    return value


async def list_all() -> dict[FeatureType, FeatureValue]:
    merged = _default_state_map()
    overrides = await _get_all_overrides()
    for feature in FEATURE_FLAGS:
        merged[feature] = overrides.get(feature, _DEFAULT_STATES[feature])
    return merged
