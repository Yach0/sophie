from __future__ import annotations

import hashlib
import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, TypedDict, cast, get_args

from sentry_sdk import feature_flags as sentry_feature_flags

from sophie_bot.db.models.feature_flag import FeatureFlagOverride, FeatureFlagOverrideSource
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
    "notes_rag_list_search",
    "filters",
    "antiflood",
    "locks",
    "welcomecaptcha",
    "welcomecaptcha_autokick",
    "op_task",
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
    "ai_chatbot_rich_markdown",
    "ai_chatbot_rich_streaming",
]


# Derived from FeatureType; defaults and validation metadata live in _FEATURE_DEFINITIONS.
FEATURE_FLAGS: Final[tuple[FeatureType, ...]] = get_args(FeatureType)


FeatureValue = bool | str | int | float
FeatureValueKind = Literal["plain", "ai_model", "service_tier", "search_provider"]


class FeatureDefinition(TypedDict):
    default: FeatureValue
    value_kind: FeatureValueKind


def get_default_value(feature: FeatureType) -> FeatureValue:
    return _DEFAULT_STATES[feature]


def get_value_kind(feature: FeatureType) -> FeatureValueKind:
    return _FEATURE_DEFINITIONS[feature]["value_kind"]


def get_allowed_string_values(
    feature: FeatureType, *, ai_model_names: frozenset[str] | None = None
) -> frozenset[str] | None:
    value_kind = get_value_kind(feature)
    if value_kind == "ai_model":
        return ai_model_names
    if value_kind == "service_tier":
        return _SERVICE_TIER_VALUES
    if value_kind == "search_provider":
        return _SEARCH_PROVIDER_VALUES
    return None


def is_valid_value_type(feature: FeatureType, value: FeatureValue) -> bool:
    default_value = get_default_value(feature)
    return type(value) is type(default_value)


def parse_feature_value(raw: str) -> FeatureValue:
    """Parse a user-provided string into a FeatureValue.

    This is the canonical string-to-FeatureValue parser.
    ``_parse_override`` delegates here after normalising bytes/None input,
    and ``_serialize_value`` is its logical inverse.
    """
    normalized_value = raw.lower()
    if normalized_value in {"true", "1"}:
        return True
    if normalized_value in {"false", "0"}:
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


class FeatureRollout(TypedDict):
    start_percentage: int
    target_percentage: int
    # Stored as ISO string so FeatureRollout is JSON-serialisable for Redis/DB without custom hooks.
    # The tradeoff is that consumers must call _parse_datetime() when computing timed percentages.
    started_at: str
    duration_days: int | None
    value: FeatureValue


class ChatFeatureOverride(TypedDict):
    chat_tid: int
    feature: FeatureType
    value: FeatureValue
    source: FeatureFlagOverrideSource


_PLAIN_FEATURE: Final[FeatureValueKind] = "plain"
_AI_MODEL_FEATURE: Final[FeatureValueKind] = "ai_model"
_SERVICE_TIER_FEATURE: Final[FeatureValueKind] = "service_tier"
_SEARCH_PROVIDER_FEATURE: Final[FeatureValueKind] = "search_provider"
_SERVICE_TIER_VALUES: Final[frozenset[str]] = frozenset({"none", "auto", "default", "flex", "priority"})
_SEARCH_PROVIDER_VALUES: Final[frozenset[str]] = frozenset({"kagi"})


def _feature(default: FeatureValue, value_kind: FeatureValueKind = _PLAIN_FEATURE) -> FeatureDefinition:
    return {"default": default, "value_kind": value_kind}


_FEATURE_DEFINITIONS: Final[dict[FeatureType, FeatureDefinition]] = {
    "ai_summary_model": _feature("openai/gpt-5.5", _AI_MODEL_FEATURE),
    "ai_filter_handler_model": _feature("openai/gpt-5-nano", _AI_MODEL_FEATURE),
    "ai_chatbot_model": _feature("", _AI_MODEL_FEATURE),
    "ai_translation_model": _feature("", _AI_MODEL_FEATURE),
    "ai_search_provider": _feature("kagi", _SEARCH_PROVIDER_FEATURE),
    "ai_chatbot_system_prompt": _feature(
        "You're a telegram bot named Sophie.\nBe funny when the topic is casual.\nSend short messages unless longer explanations are needed.\nDo not reply to many messages at once, focus on the latest message only.\nPrefer to search information in the internet"
    ),
    "ai_translate_system_prompt": _feature(
        "You're a professional AI translator / transcriber.\nSet translation_explanations to null unless the source is ambiguous, self-contradictory, requires culturally/contextually essential explanation, contains untranslatable idiom/wordplay/polysemy affecting meaning, or needs disambiguation of a proper noun/technical term/abbreviation;if included, keep it concise (≤2 factual sentences)."
    ),
    "ai_chat_summaries_prompt": _feature(
        "Summarize the chat day into one short general overview and several topic lines.\nEach topic line must contain a short title, one fitting emoji, and the list of source message IDs.\nDo not include any IDs that are not present in the provided transcript.\nSkip one-off chatter that does not form a meaningful discussion.\nPrefer topics that include at least three messages or at least two participants, and avoid weak one-person fragments."
    ),
    "ai_moderation_reason_prompt": _feature(
        "Generate a brief, professional moderation reason for restricting a user based on their message."
    ),
    "ai_filter_suggestions_prompt": _feature(
        "You generate Sophie Bot filter handler suggestions.\nReturn 1 to 3 unique suggestions as structured data."
    ),
    "ai_chatbot": _feature(True),
    "ai_chatbot_admin_status": _feature(False),
    "ai_chatbot_chat_name": _feature(False),
    "ai_chatbot_blockquote": _feature(True),
    "ai_chatbot_research_quote": _feature(True),
    "ai_chatbot_thinking_message": _feature(False),
    "ai_chatbot_tool_thinking": _feature(False),
    "ai_chatbot_random_emoji": _feature(False),
    "ai_chatbot_streaming": _feature(False),
    "ai_chatbot_streaming_backoff_seconds": _feature(1.5),
    "ai_chatbot_request_limit": _feature(8),
    "ai_chatbot_tool_calls_limit": _feature(12),
    "ai_chatbot_response_tokens_limit": _feature(0),
    "ai_translations": _feature(True),
    "ai_moderation": _feature(True),
    "ai_moderation_reasons": _feature(True),
    "ai_filters": _feature(True),
    "ai_chat_summaries": _feature(True),
    "ai_system_prompt_summaries": _feature(False),
    "ai_notes_related_system_prompt": _feature(False),
    "ai_notes_related_system_prompt_full_content": _feature(False),
    "ai_agent_save_notes": _feature(False),
    "ai_memories_to_notes": _feature(False),
    "ai_delete_notes": _feature(False),
    "notes_rag_embeddings": _feature(False),
    "notes_rag_list_search": _feature(False),
    "filters": _feature(True),
    "antiflood": _feature(True),
    "locks": _feature(True),
    "welcomecaptcha": _feature(True),
    "welcomecaptcha_autokick": _feature(True),
    "op_task": _feature(False),
    "ai_chatbot_service_tier": _feature("none", _SERVICE_TIER_FEATURE),
    "ai_translations_service_tier": _feature("none", _SERVICE_TIER_FEATURE),
    "ai_filters_service_tier": _feature("none", _SERVICE_TIER_FEATURE),
    "ai_chat_summaries_service_tier": _feature("none", _SERVICE_TIER_FEATURE),
    "ai_proactive_replies": _feature(False),
    "ai_proactive_replies_model": _feature("openai/gpt-5-nano", _AI_MODEL_FEATURE),
    "ai_proactive_replies_prompt": _feature(
        "Be very conservative. Most batches should result in no action. Only answer if Sophie was clearly invited into the conversation, someone asks an open question that Sophie can help with, or there is a very strong natural opportunity for a short useful/funny reply. Do not answer generic chatter, small talk, arguments, moderation/admin topics, old topics, or messages that already moved on. Prefer no action over a mediocre answer. If answering, be brief: 1-2 short sentences, casual, no long explanations, no lists unless explicitly needed. React only when the reaction is obviously appropriate and lightweight. Never try to participate in every topic."
    ),
    "ai_proactive_replies_service_tier": _feature("flex", _SERVICE_TIER_FEATURE),
    "ai_proactive_replies_batch_size": _feature(30),
    "ai_proactive_replies_window_seconds": _feature(180),
    "ai_proactive_replies_max_answers": _feature(1),
    "ai_proactive_replies_max_reactions": _feature(1),
    "ai_proactive_replies_min_messages": _feature(30),
    "ai_research": _feature(False),
    "ai_research_model": _feature("openai/gpt-5.5", _AI_MODEL_FEATURE),
    "ai_research_max_rounds": _feature(3),
    "ai_research_queries_per_round": _feature(5),
    "ai_research_results_per_query": _feature(5),
    "ai_research_service_tier": _feature("flex", _SERVICE_TIER_FEATURE),
    "ai_chatbot_rich_markdown": _feature(False),
    "ai_chatbot_rich_streaming": _feature(False),
}

_DEFAULT_STATES: Final[dict[FeatureType, FeatureValue]] = {
    feature: definition["default"] for feature, definition in _FEATURE_DEFINITIONS.items()
}

assert set(FEATURE_FLAGS) == set(_DEFAULT_STATES), "FeatureType and _DEFAULT_STATES must define the same flags"


# Redis keys use legacy "kill_switch" naming for backward compatibility.
# The feature flag system was renamed from "kill switch" but the Redis keys
# were kept to avoid a data migration.
_REDIS_KEY: Final[str] = "sophie:kill_switch"
_REDIS_CHAT_KEY_PREFIX: Final[str] = "sophie:kill_switch_chat"
_REDIS_ROLLOUT_KEY: Final[str] = "sophie:kill_switch_rollout"
_ROLLOUT_FEATURE_PREFIX: Final[str] = "__rollout__:"
_TRUE_VALUE: Final[str] = "1"
_FALSE_VALUE: Final[str] = "0"


def _chat_redis_key(chat_tid: int) -> str:
    return f"{_REDIS_CHAT_KEY_PREFIX}:{chat_tid}"


def _rollout_storage_feature(feature: FeatureType) -> str:
    return f"{_ROLLOUT_FEATURE_PREFIX}{feature}"


def _serialize_value(value: FeatureValue) -> str:
    """Serialize a FeatureValue for Redis storage (inverse of parse_feature_value)."""
    if isinstance(value, bool):
        return _TRUE_VALUE if value else _FALSE_VALUE
    return str(value)


def _serialize_rollout(rollout: FeatureRollout) -> str:
    return json.dumps(rollout, separators=(",", ":"))


def _parse_override(value: bytes | str | None, default: FeatureValue) -> FeatureValue | None:
    if value is None:
        return None
    normalized_value = value.decode() if isinstance(value, bytes) else value
    parsed = parse_feature_value(normalized_value)
    # When the default is bool, an unparsable string should not fall through as a raw string.
    if parsed == normalized_value and isinstance(default, bool):
        return None
    return parsed


def _coerce_db_value(value: Any) -> FeatureValue | None:
    if isinstance(value, bool | str | int | float):
        return value
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed_value = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed_value.tzinfo is None:
        return parsed_value.replace(tzinfo=UTC)
    return parsed_value.astimezone(UTC)


def _coerce_percentage(value: Any) -> int | None:
    if not isinstance(value, int) or not 0 <= value <= 100:
        return None
    return value


def _validate_rollout_percentage(percentage: int) -> None:
    if not 0 <= percentage <= 100:
        msg = "Rollout percentage must be between 0 and 100."
        raise ValueError(msg)


def _validate_rollout_days(days: int) -> None:
    if days <= 0:
        msg = "Rollout days must be greater than 0."
        raise ValueError(msg)


def _coerce_rollout(value: Any) -> FeatureRollout | None:
    if isinstance(value, bytes):
        value = value.decode()

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None

    if not isinstance(value, dict):
        return None

    raw_value = value.get("value")
    rollout_value = _coerce_db_value(raw_value)
    if rollout_value is None:
        return None

    legacy_percentage = _coerce_percentage(value.get("percentage"))
    # TODO: Remove legacy percentage migration after 2026-09 if no old-format rollouts remain.
    if legacy_percentage is not None:
        return {
            "start_percentage": legacy_percentage,
            "target_percentage": legacy_percentage,
            "started_at": _serialize_datetime(_utc_now()),
            "duration_days": None,
            "value": rollout_value,
        }

    start_percentage = _coerce_percentage(value.get("start_percentage"))
    target_percentage = _coerce_percentage(value.get("target_percentage"))
    started_at = _parse_datetime(value.get("started_at"))
    raw_duration_days = value.get("duration_days")

    if raw_duration_days is not None and (not isinstance(raw_duration_days, int) or raw_duration_days <= 0):
        return None
    if start_percentage is None or target_percentage is None or started_at is None:
        return None

    return {
        "start_percentage": start_percentage,
        "target_percentage": target_percentage,
        "started_at": _serialize_datetime(started_at),
        "duration_days": raw_duration_days,
        "value": rollout_value,
    }


def get_rollout_percentage(rollout: FeatureRollout, now: datetime | None = None) -> int:
    if rollout["duration_days"] is None:
        return rollout["target_percentage"]

    current_time = now or _utc_now()
    started_at = _parse_datetime(rollout["started_at"])
    if started_at is None:
        return rollout["start_percentage"]

    duration = timedelta(days=rollout["duration_days"])
    elapsed = current_time.astimezone(UTC) - started_at
    if elapsed <= timedelta(0):
        return rollout["start_percentage"]
    if elapsed >= duration:
        return rollout["target_percentage"]

    progress = elapsed.total_seconds() / duration.total_seconds()
    percentage = rollout["start_percentage"] + (rollout["target_percentage"] - rollout["start_percentage"]) * progress
    return int(percentage)


def _chat_rollout_bucket(feature: FeatureType, chat_tid: int) -> int:
    digest = hashlib.sha256(f"{feature}:{chat_tid}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big") % 100


def _is_chat_in_rollout(feature: FeatureType, chat_tid: int, percentage: int) -> bool:
    return percentage > 0 and _chat_rollout_bucket(feature, chat_tid) < percentage


async def _resolve_cached_override(
    redis_key: str, feature: FeatureType, *, chat_tid: int | None = None
) -> FeatureValue | None:
    """Resolve a feature flag override: Redis cache → DB fallback, warming the cache on DB hit."""
    value = await aredis.hget(redis_key, feature)  # ty: ignore[invalid-await]
    parsed_value = _parse_override(value, _DEFAULT_STATES[feature])
    if parsed_value is not None:
        return parsed_value

    override = await FeatureFlagOverride.get_override(feature, chat_tid=chat_tid)
    if override is None:
        return None

    db_value = _coerce_db_value(override.value)
    if db_value is not None:
        await aredis.hset(redis_key, feature, _serialize_value(db_value))  # ty: ignore[invalid-await]
    return db_value


async def _get_override(feature: FeatureType) -> FeatureValue | None:
    return await _resolve_cached_override(_REDIS_KEY, feature)


async def _set_override(feature: FeatureType, value: FeatureValue) -> None:
    await FeatureFlagOverride.set_override(feature, value)
    await aredis.hset(_REDIS_KEY, feature, _serialize_value(value))  # ty: ignore[invalid-await]


async def _get_all_overrides() -> dict[FeatureType, FeatureValue]:
    parsed_overrides: dict[FeatureType, FeatureValue] = {}
    cached_overrides: dict[str, str] = {}

    async for override in FeatureFlagOverride.find({"chat_tid": None}):  # deepsource-ignore[PYL-E1133]
        if override.feature not in FEATURE_FLAGS:
            continue

        typed_feature = cast(FeatureType, override.feature)
        parsed_value = _coerce_db_value(override.value)
        if parsed_value is None:
            continue

        parsed_overrides[typed_feature] = parsed_value
        cached_overrides[typed_feature] = _serialize_value(parsed_value)

    await _cache_serialized_values(_REDIS_KEY, cached_overrides)

    return parsed_overrides


async def _cache_serialized_values(redis_key: str, values: Mapping[str, str]) -> None:
    if not values:
        return
    await asyncio.gather(  # ty: ignore[no-matching-overload]
        *[aredis.hset(redis_key, feature, value) for feature, value in values.items()]
    )


def _track_feature_in_sentry(feature: FeatureType, enabled: bool) -> None:
    sentry_feature_flags.add_feature_flag(feature, enabled)


async def get_rollout(feature: FeatureType) -> FeatureRollout | None:
    cached_value = await aredis.hget(_REDIS_ROLLOUT_KEY, feature)  # ty: ignore[invalid-await]
    parsed_cached_value = _coerce_rollout(cached_value)
    if parsed_cached_value is not None:
        return parsed_cached_value

    override = await FeatureFlagOverride.get_override(_rollout_storage_feature(feature))
    if override is None:
        return None

    rollout = _coerce_rollout(override.value)
    if rollout is not None:
        await aredis.hset(_REDIS_ROLLOUT_KEY, feature, _serialize_rollout(rollout))  # ty: ignore[invalid-await]
    return rollout


async def set_rollout(feature: FeatureType, percentage: int, value: FeatureValue) -> None:
    _validate_rollout_percentage(percentage)

    rollout: FeatureRollout = {
        "start_percentage": percentage,
        "target_percentage": percentage,
        "started_at": _serialize_datetime(_utc_now()),
        "duration_days": None,
        "value": value,
    }
    await _set_rollout(feature, rollout)


async def set_timed_rollout(
    feature: FeatureType,
    days: int,
    value: FeatureValue,
    *,
    now: datetime | None = None,
) -> None:
    _validate_rollout_days(days)

    current_rollout = await get_rollout(feature)
    start_percentage = get_rollout_percentage(current_rollout, now=now) if current_rollout is not None else 0
    rollout: FeatureRollout = {
        "start_percentage": start_percentage,
        "target_percentage": 100,
        "started_at": _serialize_datetime(now or _utc_now()),
        "duration_days": days,
        "value": value,
    }
    await _set_rollout(feature, rollout)


async def bump_rollout(feature: FeatureType, percentage: int) -> FeatureRollout:
    _validate_rollout_percentage(percentage)

    current_rollout = await get_rollout(feature)
    if current_rollout is None:
        msg = "Cannot bump rollout without an existing rollout."
        raise ValueError(msg)

    current_percentage = get_rollout_percentage(current_rollout)
    bumped_percentage = min(100, current_percentage + percentage)
    rollout: FeatureRollout = {
        "start_percentage": bumped_percentage,
        "target_percentage": bumped_percentage,
        "started_at": _serialize_datetime(_utc_now()),
        "duration_days": None,
        "value": current_rollout["value"],
    }
    await _set_rollout(feature, rollout)
    return rollout


async def _set_rollout(feature: FeatureType, rollout: FeatureRollout) -> None:
    await FeatureFlagOverride.set_override(_rollout_storage_feature(feature), rollout)
    await aredis.hset(_REDIS_ROLLOUT_KEY, feature, _serialize_rollout(rollout))  # ty: ignore[invalid-await]


async def delete_rollout(feature: FeatureType) -> None:
    """Delete rollout config while preserving rollout-created per-chat overrides.

    Rollout-created overrides are intentionally frozen on first access so
    operators can delete rollout config without changing chats that already
    entered the rollout.
    """
    await FeatureFlagOverride.delete_override(_rollout_storage_feature(feature))
    await aredis.hdel(_REDIS_ROLLOUT_KEY, feature)  # ty: ignore[invalid-await]


async def list_rollouts() -> dict[FeatureType, FeatureRollout]:
    rollouts: dict[FeatureType, FeatureRollout] = {}
    cached_rollouts: dict[str, str] = {}
    async for override in FeatureFlagOverride.find(  # deepsource-ignore[PYL-E1133]
        {"feature": {"$regex": f"^{_ROLLOUT_FEATURE_PREFIX}"}}
    ):
        feature = override.feature.removeprefix(_ROLLOUT_FEATURE_PREFIX)
        if feature not in FEATURE_FLAGS:
            continue

        typed_feature = cast(FeatureType, feature)
        rollout = _coerce_rollout(override.value)
        if rollout is None:
            continue

        rollouts[typed_feature] = rollout
        cached_rollouts[typed_feature] = _serialize_rollout(rollout)

    await _cache_serialized_values(_REDIS_ROLLOUT_KEY, cached_rollouts)

    return rollouts


async def get_chat_override(feature: FeatureType, chat_tid: int) -> FeatureValue | None:
    return await _resolve_cached_override(_chat_redis_key(chat_tid), feature, chat_tid=chat_tid)


async def set_chat_override(feature: FeatureType, chat_tid: int, value: FeatureValue) -> None:
    await _set_chat_override(feature, chat_tid, value, source="manual")


async def _set_chat_override(
    feature: FeatureType, chat_tid: int, value: FeatureValue, *, source: FeatureFlagOverrideSource
) -> None:
    await FeatureFlagOverride.set_override(feature, value, chat_tid=chat_tid, source=source)
    await aredis.hset(_chat_redis_key(chat_tid), feature, _serialize_value(value))  # ty: ignore[invalid-await]


async def delete_override(feature: FeatureType) -> None:
    await FeatureFlagOverride.delete_override(feature)
    await aredis.hdel(_REDIS_KEY, feature)  # ty: ignore[invalid-await]


async def delete_chat_override(feature: FeatureType, chat_tid: int) -> None:
    await FeatureFlagOverride.delete_override(feature, chat_tid=chat_tid)
    await aredis.hdel(_chat_redis_key(chat_tid), feature)  # ty: ignore[invalid-await]


async def list_chat_overrides(chat_tid: int) -> dict[FeatureType, FeatureValue]:
    parsed_overrides: dict[FeatureType, FeatureValue] = {}
    cached_overrides: dict[str, str] = {}
    async for override in FeatureFlagOverride.find(
        FeatureFlagOverride.chat_tid == chat_tid
    ):  # deepsource-ignore[PYL-E1133]
        if override.feature not in FEATURE_FLAGS:
            continue
        typed_feature = cast(FeatureType, override.feature)
        parsed_value = _coerce_db_value(override.value)
        if parsed_value is not None:
            parsed_overrides[typed_feature] = parsed_value
            cached_overrides[typed_feature] = _serialize_value(parsed_value)
    if cached_overrides:
        redis_key = _chat_redis_key(chat_tid)
        await _cache_serialized_values(redis_key, cached_overrides)
    return parsed_overrides


async def list_chat_override_details(chat_tid: int | None = None) -> list[ChatFeatureOverride]:
    query: dict[str, Any] = {"chat_tid": {"$ne": None}} if chat_tid is None else {"chat_tid": chat_tid}
    overrides: list[ChatFeatureOverride] = []

    async for override in FeatureFlagOverride.find(query):  # deepsource-ignore[PYL-E1133]
        if override.feature not in FEATURE_FLAGS or override.chat_tid is None:
            continue

        typed_feature = cast(FeatureType, override.feature)
        parsed_value = _coerce_db_value(override.value)
        if parsed_value is None:
            continue

        overrides.append(
            {
                "chat_tid": override.chat_tid,
                "feature": typed_feature,
                "value": parsed_value,
                "source": override.source,
            }
        )

    return sorted(overrides, key=lambda item: (item["source"], item["chat_tid"], item["feature"]))


async def get_value(feature: FeatureType, chat_tid: int | None = None) -> FeatureValue:
    """Return the effective feature value.

    If a chat qualifies for an active rollout, the rollout value is persisted
    as a rollout-sourced chat override on first access. This freezes rollout
    membership for that chat until the per-chat override is explicitly deleted.
    """
    if chat_tid is not None:
        chat_override = await get_chat_override(feature, chat_tid)
        if chat_override is not None:
            return chat_override
    override = await _get_override(feature)
    if override is not None:
        return override

    if chat_tid is not None:
        rollout = await get_rollout(feature)
        if rollout is not None and _is_chat_in_rollout(feature, chat_tid, get_rollout_percentage(rollout)):
            await _set_chat_override(feature, chat_tid, rollout["value"], source="rollout")
            return rollout["value"]

    return _DEFAULT_STATES[feature]


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
    merged = _DEFAULT_STATES.copy()
    merged.update(await _get_all_overrides())
    return merged
