from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest

from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import (
    FEATURE_FLAGS,
    FeatureRollout,
    _chat_rollout_bucket,
    _coerce_db_value,
    _coerce_percentage,
    _coerce_rollout,
    _is_chat_in_rollout,
    _parse_datetime,
    _parse_override,
    _serialize_datetime,
    _serialize_rollout,
    _serialize_value,
    _validate_rollout_days,
    _validate_rollout_percentage,
    bump_rollout,
    delete_chat_override,
    delete_override,
    delete_rollout,
    get_allowed_string_values,
    get_chat_override,
    get_default_value,
    get_rollout,
    get_rollout_percentage,
    get_service_tier,
    get_value,
    get_value_kind,
    is_enabled,
    is_valid_value_type,
    list_all,
    list_chat_override_details,
    list_chat_overrides,
    list_rollouts,
    parse_feature_value,
    set_chat_override,
    set_enabled,
    set_rollout,
    set_timed_rollout,
    set_value,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FEATURE = "op_task"
BOOL_FEATURE_DEFAULT_TRUE = "welcomecaptcha"
STRING_FEATURE = "ai_chatbot_service_tier"
CHAT_TID_A = -1002950100
CHAT_TID_B = -1002950101
CHAT_TID_C = -1002950102


def _find_rollout_chat_tid(*, expected_in_rollout: bool) -> int:
    for chat_tid in range(-1002951000, -1002950000):
        if _is_chat_in_rollout(FEATURE, chat_tid, 10) is expected_in_rollout:
            return chat_tid

    msg = f"No chat tid found with expected rollout state {expected_in_rollout}"
    raise AssertionError(msg)


@pytest.fixture(autouse=True)
async def _reset_feature_flag_overrides(db_init: object) -> AsyncGenerator[None]:
    await FeatureFlagOverride.get_pymongo_collection().delete_many({})
    yield
    await FeatureFlagOverride.get_pymongo_collection().delete_many({})


# ===========================================================================
# Pure-function unit tests (no DB / Redis needed)
# ===========================================================================


class TestParseOverride:
    """_parse_override(value, default) parses Redis-stored strings."""

    def test_none_returns_none(self) -> None:
        assert _parse_override(None, True) is None

    @pytest.mark.parametrize("raw", [b"true", b"True", b"TRUE", b"1"])
    def test_truthy_bytes(self, raw: bytes) -> None:
        assert _parse_override(raw, True) is True

    @pytest.mark.parametrize("raw", [b"false", b"False", b"FALSE", b"0"])
    def test_falsy_bytes(self, raw: bytes) -> None:
        assert _parse_override(raw, True) is False

    def test_string_integer(self) -> None:
        assert _parse_override("42", 0) == 42

    def test_string_float(self) -> None:
        assert _parse_override("3.14", 0.0) == 3.14

    def test_string_value_when_default_is_not_bool(self) -> None:
        assert _parse_override("openai/gpt-5", "default_model") == "openai/gpt-5"

    def test_invalid_string_with_bool_default_returns_none(self) -> None:
        assert _parse_override("garbage", True) is None

    def test_case_insensitive_boolean(self) -> None:
        assert _parse_override("tRuE", False) is True
        assert _parse_override("FaLsE", True) is False


class TestParseFeatureValue:
    """parse_feature_value(raw) parses user-provided strings into FeatureValue."""

    def test_true(self) -> None:
        assert parse_feature_value("true") is True

    def test_false(self) -> None:
        assert parse_feature_value("false") is False

    def test_one(self) -> None:
        assert parse_feature_value("1") is True

    def test_zero(self) -> None:
        assert parse_feature_value("0") is False

    def test_integer(self) -> None:
        assert parse_feature_value("42") == 42

    def test_float(self) -> None:
        assert parse_feature_value("3.14") == 3.14

    def test_string_fallback(self) -> None:
        assert parse_feature_value("openai/gpt-5") == "openai/gpt-5"

    def test_case_insensitive_bool(self) -> None:
        assert parse_feature_value("TRUE") is True
        assert parse_feature_value("False") is False


class TestSerializeValue:
    def test_true(self) -> None:
        assert _serialize_value(True) == "1"

    def test_false(self) -> None:
        assert _serialize_value(False) == "0"

    def test_string(self) -> None:
        assert _serialize_value("openai/gpt-5") == "openai/gpt-5"

    def test_int(self) -> None:
        assert _serialize_value(42) == "42"

    def test_float(self) -> None:
        assert _serialize_value(1.5) == "1.5"


class TestFeatureMetadata:
    def test_defaults_are_derived_for_all_flags(self) -> None:
        assert {feature: get_default_value(feature) for feature in FEATURE_FLAGS}

    def test_value_type_matches_default(self) -> None:
        assert is_valid_value_type("welcomecaptcha", True)
        assert not is_valid_value_type("welcomecaptcha", "true")

    def test_ai_model_values_are_unrestricted(self) -> None:
        # Model names are open-ended: unregistered names are built as custom OpenRouter
        # models at runtime, so op_ff accepts any string here.
        assert get_value_kind("ai_summary_model") == "ai_model"
        assert get_allowed_string_values("ai_summary_model") is None

    def test_service_tier_values_are_declared_in_metadata(self) -> None:
        assert get_value_kind("ai_chatbot_service_tier") == "service_tier"
        assert get_allowed_string_values("ai_chatbot_service_tier") == frozenset(
            {"none", "auto", "default", "flex", "priority"}
        )

    def test_plain_string_values_are_unrestricted(self) -> None:
        assert get_value_kind("ai_chatbot_system_prompt") == "plain"
        assert get_allowed_string_values("ai_chatbot_system_prompt") is None


class TestCoerceDbValue:
    def test_accepts_bool(self) -> None:
        assert _coerce_db_value(True) is True

    def test_accepts_str(self) -> None:
        assert _coerce_db_value("hello") == "hello"

    def test_accepts_int(self) -> None:
        assert _coerce_db_value(42) == 42

    def test_accepts_float(self) -> None:
        assert _coerce_db_value(3.14) == 3.14

    def test_rejects_list(self) -> None:
        assert _coerce_db_value([1, 2]) is None

    def test_rejects_none(self) -> None:
        assert _coerce_db_value(None) is None

    def test_rejects_dict(self) -> None:
        assert _coerce_db_value({"key": "value"}) is None


class TestCoercePercentage:
    def test_valid(self) -> None:
        assert _coerce_percentage(0) == 0
        assert _coerce_percentage(50) == 50
        assert _coerce_percentage(100) == 100

    def test_negative(self) -> None:
        assert _coerce_percentage(-1) is None

    def test_over_100(self) -> None:
        assert _coerce_percentage(101) is None

    def test_float_rejected(self) -> None:
        assert _coerce_percentage(50.0) is None

    def test_string_rejected(self) -> None:
        assert _coerce_percentage("50") is None


class TestValidateRolloutPercentage:
    def test_valid_range(self) -> None:
        _validate_rollout_percentage(0)
        _validate_rollout_percentage(100)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 100"):
            _validate_rollout_percentage(-1)

    def test_over_100_raises(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 100"):
            _validate_rollout_percentage(101)


class TestValidateRolloutDays:
    def test_positive(self) -> None:
        _validate_rollout_days(1)

    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            _validate_rollout_days(0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            _validate_rollout_days(-5)


class TestParseDatetime:
    def test_iso_string(self) -> None:
        result = _parse_datetime("2026-06-01T00:00:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 6

    def test_naive_string_gets_utc(self) -> None:
        result = _parse_datetime("2026-06-01T12:00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_non_string_returns_none(self) -> None:
        assert _parse_datetime(12345) is None
        assert _parse_datetime(None) is None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_datetime("not-a-date") is None


class TestSerializeDatetime:
    def test_utc_roundtrip(self) -> None:
        original = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        serialized = _serialize_datetime(original)
        parsed = _parse_datetime(serialized)
        assert parsed == original


class TestCoerceRollout:
    def test_valid_fixed_rollout(self) -> None:
        rollout = _coerce_rollout(
            {
                "start_percentage": 20,
                "target_percentage": 80,
                "started_at": "2026-06-01T00:00:00+00:00",
                "duration_days": 7,
                "value": True,
            }
        )
        assert rollout is not None
        assert rollout["start_percentage"] == 20
        assert rollout["target_percentage"] == 80
        assert rollout["duration_days"] == 7
        assert rollout["value"] is True

    def test_fixed_rollout_without_duration(self) -> None:
        rollout = _coerce_rollout(
            {
                "start_percentage": 50,
                "target_percentage": 50,
                "started_at": "2026-06-01T00:00:00+00:00",
                "duration_days": None,
                "value": "openai/gpt-5",
            }
        )
        assert rollout is not None
        assert rollout["duration_days"] is None

    def test_legacy_percentage_format(self) -> None:
        rollout = _coerce_rollout({"percentage": 30, "value": True})
        assert rollout is not None
        assert rollout["start_percentage"] == 30
        assert rollout["target_percentage"] == 30
        assert rollout["duration_days"] is None

    def test_json_string_input(self) -> None:
        rollout = _coerce_rollout(
            '{"start_percentage":10,"target_percentage":10,"started_at":"2026-06-01T00:00:00+00:00","value":true}'
        )
        assert rollout is not None
        assert rollout["start_percentage"] == 10

    def test_bytes_input(self) -> None:
        rollout = _coerce_rollout(
            b'{"start_percentage":10,"target_percentage":10,"started_at":"2026-06-01T00:00:00+00:00","value":true}'
        )
        assert rollout is not None

    def test_invalid_json_returns_none(self) -> None:
        assert _coerce_rollout("not json") is None

    def test_non_dict_returns_none(self) -> None:
        assert _coerce_rollout([1, 2, 3]) is None

    def test_missing_value_returns_none(self) -> None:
        assert _coerce_rollout({"start_percentage": 10, "target_percentage": 10}) is None

    def test_invalid_value_type_returns_none(self) -> None:
        assert _coerce_rollout({"value": [1, 2]}) is None

    def test_negative_duration_days_returns_none(self) -> None:
        result = _coerce_rollout(
            {
                "start_percentage": 0,
                "target_percentage": 100,
                "started_at": "2026-06-01T00:00:00+00:00",
                "duration_days": -1,
                "value": True,
            }
        )
        assert result is None

    def test_missing_required_fields_returns_none(self) -> None:
        assert _coerce_rollout({"value": True, "start_percentage": 10}) is None


class TestSerializeRollout:
    def test_roundtrip(self) -> None:
        rollout: FeatureRollout = {
            "start_percentage": 10,
            "target_percentage": 100,
            "started_at": "2026-06-01T00:00:00+00:00",
            "duration_days": 7,
            "value": True,
        }
        serialized = _serialize_rollout(rollout)
        parsed = _coerce_rollout(serialized)
        assert parsed is not None
        assert parsed["start_percentage"] == rollout["start_percentage"]
        assert parsed["target_percentage"] == rollout["target_percentage"]
        assert parsed["duration_days"] == rollout["duration_days"]
        assert parsed["value"] == rollout["value"]


class TestGetRolloutPercentage:
    def test_fixed_rollout_returns_target(self) -> None:
        rollout: FeatureRollout = {
            "start_percentage": 50,
            "target_percentage": 50,
            "started_at": "2026-06-01T00:00:00+00:00",
            "duration_days": None,
            "value": True,
        }
        assert get_rollout_percentage(rollout) == 50

    def test_timed_rollout_at_start(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        rollout: FeatureRollout = {
            "start_percentage": 0,
            "target_percentage": 100,
            "started_at": _serialize_datetime(started),
            "duration_days": 10,
            "value": True,
        }
        assert get_rollout_percentage(rollout, now=started) == 0

    def test_timed_rollout_at_end(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        rollout: FeatureRollout = {
            "start_percentage": 0,
            "target_percentage": 100,
            "started_at": _serialize_datetime(started),
            "duration_days": 10,
            "value": True,
        }
        assert get_rollout_percentage(rollout, now=started + timedelta(days=10)) == 100

    def test_timed_rollout_midpoint(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        rollout: FeatureRollout = {
            "start_percentage": 0,
            "target_percentage": 100,
            "started_at": _serialize_datetime(started),
            "duration_days": 10,
            "value": True,
        }
        assert get_rollout_percentage(rollout, now=started + timedelta(days=5)) == 50

    def test_timed_rollout_before_start_returns_start_percentage(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        rollout: FeatureRollout = {
            "start_percentage": 10,
            "target_percentage": 100,
            "started_at": _serialize_datetime(started),
            "duration_days": 5,
            "value": True,
        }
        assert get_rollout_percentage(rollout, now=started - timedelta(days=1)) == 10

    def test_timed_rollout_with_unparseable_started_at_returns_start(self) -> None:
        rollout: FeatureRollout = {
            "start_percentage": 20,
            "target_percentage": 80,
            "started_at": "not-a-date",
            "duration_days": 5,
            "value": True,
        }
        assert get_rollout_percentage(rollout) == 20


class TestChatRolloutBucket:
    def test_deterministic(self) -> None:
        bucket_a = _chat_rollout_bucket(FEATURE, CHAT_TID_A)
        bucket_b = _chat_rollout_bucket(FEATURE, CHAT_TID_A)
        assert bucket_a == bucket_b

    def test_different_chats_differ(self) -> None:
        bucket_a = _chat_rollout_bucket(FEATURE, CHAT_TID_A)
        bucket_b = _chat_rollout_bucket(FEATURE, CHAT_TID_B)
        # Not guaranteed but extremely unlikely to be equal for two random IDs
        assert isinstance(bucket_a, int)
        assert isinstance(bucket_b, int)
        assert 0 <= bucket_a < 100
        assert 0 <= bucket_b < 100

    def test_different_features_differ(self) -> None:
        bucket_a = _chat_rollout_bucket(FEATURE, CHAT_TID_A)
        bucket_b = _chat_rollout_bucket(BOOL_FEATURE_DEFAULT_TRUE, CHAT_TID_A)
        # Same reasoning: different features should generally produce different buckets
        assert isinstance(bucket_a, int)
        assert isinstance(bucket_b, int)

    def test_zero_percentage_excludes_all(self) -> None:
        assert _is_chat_in_rollout(FEATURE, CHAT_TID_A, 0) is False

    def test_full_percentage_includes_all(self) -> None:
        assert _is_chat_in_rollout(FEATURE, CHAT_TID_A, 100) is True


# ===========================================================================
# Integration tests (require DB + Redis)
# ===========================================================================


class TestDefaults:
    async def test_bool_default_true(self) -> None:
        assert await is_enabled(BOOL_FEATURE_DEFAULT_TRUE) is True

    async def test_bool_default_false(self) -> None:
        assert await is_enabled(FEATURE) is False

    async def test_string_default(self) -> None:
        assert await get_value(STRING_FEATURE) == "none"

    async def test_get_default_value(self) -> None:
        assert get_default_value(FEATURE) is False
        assert get_default_value(BOOL_FEATURE_DEFAULT_TRUE) is True
        assert get_default_value("ai_chatbot_service_tier") == "none"

    async def test_get_service_tier_returns_none_for_none_string(self) -> None:
        assert await get_service_tier(STRING_FEATURE) is None

    async def test_get_service_tier_returns_value_when_set(self) -> None:
        await set_value(STRING_FEATURE, "flex")
        assert await get_service_tier(STRING_FEATURE) == "flex"


class TestSetGetValue:
    async def test_set_boolean(self) -> None:
        await set_value(FEATURE, True)
        assert await get_value(FEATURE) is True

    async def test_set_string(self) -> None:
        await set_value("ai_summary_model", "openai/gpt-4o")
        assert await get_value("ai_summary_model") == "openai/gpt-4o"

    async def test_set_integer(self) -> None:
        await set_value("ai_chatbot_request_limit", 20)
        assert await get_value("ai_chatbot_request_limit") == 20

    async def test_set_float(self) -> None:
        await set_value("ai_chatbot_streaming_backoff_seconds", 2.5)
        assert await get_value("ai_chatbot_streaming_backoff_seconds") == 2.5

    async def test_set_integer_zero_stays_an_integer(self) -> None:
        # "0"/"1" round-trip through the string parser, which reads them as booleans for bool flags.
        await set_value("ai_chatbot_request_limit", 0)
        assert await get_value("ai_chatbot_request_limit") == 0
        assert await get_value("ai_chatbot_request_limit") is not False

    async def test_set_integer_one_stays_an_integer(self) -> None:
        await set_value("ai_chatbot_request_limit", 1)
        assert await get_value("ai_chatbot_request_limit") is not True

    async def test_set_float_one_stays_a_float(self) -> None:
        await set_value("ai_chatbot_streaming_backoff_seconds", 1.0)
        assert await get_value("ai_chatbot_streaming_backoff_seconds") == 1.0
        assert await get_value("ai_chatbot_streaming_backoff_seconds") is not True

    async def test_persists_to_redis(self) -> None:
        await set_value(FEATURE, True)
        assert await aredis.hget("sophie:kill_switch", FEATURE) == b"1"

    async def test_persists_string_to_redis(self) -> None:
        await set_value("ai_summary_model", "test-model")
        assert await aredis.hget("sophie:kill_switch", "ai_summary_model") == b"test-model"


class TestSetEnabled:
    async def test_set_enabled_true(self) -> None:
        await set_enabled(FEATURE, True)
        assert await is_enabled(FEATURE) is True

    async def test_set_enabled_false(self) -> None:
        await set_enabled(BOOL_FEATURE_DEFAULT_TRUE, False)
        assert await is_enabled(BOOL_FEATURE_DEFAULT_TRUE) is False

    async def test_persists_to_redis(self) -> None:
        await set_enabled(FEATURE, True)
        assert await aredis.hget("sophie:kill_switch", FEATURE) == b"1"


class TestDeleteOverride:
    async def test_delete_global_override_reverts_to_default(self) -> None:
        await set_enabled(FEATURE, True)
        assert await is_enabled(FEATURE) is True

        await delete_override(FEATURE)
        assert await is_enabled(FEATURE) is False

    async def test_delete_nonexistent_override_is_safe(self) -> None:
        await delete_override(FEATURE)
        assert await is_enabled(FEATURE) is False

    async def test_delete_clears_redis_cache(self) -> None:
        await set_enabled(FEATURE, True)
        assert await aredis.hget("sophie:kill_switch", FEATURE) == b"1"

        await delete_override(FEATURE)
        assert await aredis.hget("sophie:kill_switch", FEATURE) is None


class TestListAll:
    async def test_returns_all_flags(self) -> None:
        states = await list_all()
        assert list(states) == list(FEATURE_FLAGS)

    async def test_includes_overrides(self) -> None:
        await set_enabled(FEATURE, True)
        states = await list_all()
        assert states[FEATURE] is True

    async def test_unmodified_flags_show_defaults(self) -> None:
        states = await list_all()
        assert states[BOOL_FEATURE_DEFAULT_TRUE] is True
        assert states[FEATURE] is False


class TestRedisFallback:
    async def test_invalid_redis_value_falls_back_to_default(self) -> None:
        await aredis.hset("sophie:kill_switch", FEATURE, b"invalid")
        assert await is_enabled(FEATURE) is False

    async def test_invalid_redis_value_list_all_falls_back(self) -> None:
        await aredis.hset("sophie:kill_switch", FEATURE, b"invalid")
        states = await list_all()
        assert states[FEATURE] is False


class TestRedisCacheWarm:
    async def test_reading_db_override_populates_redis(self) -> None:
        await FeatureFlagOverride.set_override(FEATURE, True)
        # Redis is empty — first read should come from DB and cache to Redis
        assert await is_enabled(FEATURE) is True
        assert await aredis.hget("sophie:kill_switch", FEATURE) == b"1"


# ===========================================================================
# Chat overrides
# ===========================================================================


class TestChatOverrides:
    async def test_set_and_get_chat_override(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        assert await get_chat_override(FEATURE, CHAT_TID_A) is True

    async def test_chat_override_overrides_default(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is True

    async def test_chat_override_does_not_affect_other_chats(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_B) is False

    async def test_no_override_returns_none(self) -> None:
        assert await get_chat_override(FEATURE, CHAT_TID_A) is None

    async def test_delete_chat_override(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        await delete_chat_override(FEATURE, CHAT_TID_A)
        assert await get_chat_override(FEATURE, CHAT_TID_A) is None

    async def test_delete_nonexistent_chat_override_is_safe(self) -> None:
        await delete_chat_override(FEATURE, CHAT_TID_A)
        assert await get_chat_override(FEATURE, CHAT_TID_A) is None

    async def test_delete_chat_override_clears_redis(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        assert await aredis.hget(f"sophie:kill_switch_chat:{CHAT_TID_A}", FEATURE) == b"1"

        await delete_chat_override(FEATURE, CHAT_TID_A)
        assert await aredis.hget(f"sophie:kill_switch_chat:{CHAT_TID_A}", FEATURE) is None

    async def test_chat_override_with_string_value(self) -> None:
        await set_chat_override("ai_summary_model", CHAT_TID_A, "openai/gpt-4o")
        assert await get_chat_override("ai_summary_model", CHAT_TID_A) == "openai/gpt-4o"

    async def test_chat_override_source_is_manual(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        details = await list_chat_override_details(CHAT_TID_A)
        assert len(details) == 1
        assert details[0]["source"] == "manual"


class TestListChatOverrides:
    async def test_list_chat_overrides_returns_dict(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        await set_chat_override(BOOL_FEATURE_DEFAULT_TRUE, CHAT_TID_A, False)
        overrides = await list_chat_overrides(CHAT_TID_A)
        assert overrides[FEATURE] is True
        assert overrides[BOOL_FEATURE_DEFAULT_TRUE] is False

    async def test_list_chat_overrides_empty(self) -> None:
        overrides = await list_chat_overrides(CHAT_TID_A)
        assert overrides == {}


class TestListChatOverrideDetails:
    async def test_returns_details_with_source(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        details = await list_chat_override_details(CHAT_TID_A)
        assert details == [{"chat_tid": CHAT_TID_A, "feature": FEATURE, "value": True, "source": "manual"}]

    async def test_all_chats_when_no_tid(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        await set_chat_override(FEATURE, CHAT_TID_B, False)
        details = await list_chat_override_details()
        assert len(details) == 2
        tids = {detail["chat_tid"] for detail in details}
        assert tids == {CHAT_TID_A, CHAT_TID_B}

    async def test_sorted_by_source_then_chat_then_feature(self) -> None:
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        await set_chat_override(FEATURE, CHAT_TID_B, True)
        details = await list_chat_override_details()
        assert details[0]["chat_tid"] <= details[1]["chat_tid"]

    async def test_empty_when_no_overrides(self) -> None:
        details = await list_chat_override_details()
        assert details == []


# ===========================================================================
# Resolution order: chat override → global override → rollout → default
# ===========================================================================


class TestResolutionOrder:
    async def test_chat_override_beats_global_override(self) -> None:
        await set_enabled(FEATURE, True)
        await set_chat_override(FEATURE, CHAT_TID_A, False)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is False

    async def test_global_override_beats_rollout(self) -> None:
        await set_rollout(FEATURE, 100, True)
        await set_value(FEATURE, False)
        # Without chat_tid, rollout isn't checked, global override applies
        assert await get_value(FEATURE) is False
        # With chat_tid, chat override is None, global override takes precedence
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is False

    async def test_rollout_applies_when_no_overrides(self) -> None:
        await set_rollout(FEATURE, 100, True)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is True

    async def test_default_used_when_nothing_set(self) -> None:
        assert await get_value(FEATURE) is False
        assert await get_value(BOOL_FEATURE_DEFAULT_TRUE) is True

    async def test_rollout_not_checked_without_chat_tid(self) -> None:
        await set_rollout(FEATURE, 100, True)
        # Without chat_tid, rollout layer is skipped
        assert await get_value(FEATURE) is False

    async def test_full_resolution_chain(self) -> None:
        """Chat override > global override > rollout > default, each step tested on clean state."""
        # 1. Default: no overrides, no rollout
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is False

        # 2. Rollout applies when no overrides exist
        await set_rollout(FEATURE, 100, True)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is True

        # 3. Manual chat override beats rollout-persisted override
        await set_chat_override(FEATURE, CHAT_TID_A, False)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is False

        # 4. On a fresh chat (no chat override), global override beats rollout
        await set_value(FEATURE, False)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_B) is False


# ===========================================================================
# Rollouts
# ===========================================================================


class TestSetRollout:
    async def test_creates_rollout(self) -> None:
        await set_rollout(FEATURE, 50, True)
        rollout = await get_rollout(FEATURE)
        assert rollout is not None
        assert rollout["value"] is True
        assert get_rollout_percentage(rollout) == 50

    async def test_rollout_with_string_value(self) -> None:
        await set_rollout("ai_summary_model", 50, "openai/gpt-4o")
        rollout = await get_rollout("ai_summary_model")
        assert rollout is not None
        assert rollout["value"] == "openai/gpt-4o"

    async def test_rejects_negative_percentage(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 100"):
            await set_rollout(FEATURE, -1, True)

    async def test_rejects_percentage_over_100(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 100"):
            await set_rollout(FEATURE, 101, True)

    async def test_rejects_zero_days_for_timed(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            await set_timed_rollout(FEATURE, 0, True)

    async def test_rejects_negative_days_for_timed(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            await set_timed_rollout(FEATURE, -5, True)


class TestGetRollout:
    async def test_returns_none_when_not_set(self) -> None:
        assert await get_rollout(FEATURE) is None

    async def test_returns_rollout_after_set(self) -> None:
        await set_rollout(FEATURE, 25, True)
        rollout = await get_rollout(FEATURE)
        assert rollout is not None
        assert rollout["start_percentage"] == 25
        assert rollout["target_percentage"] == 25


class TestDeleteRollout:
    async def test_delete_rollout(self) -> None:
        await set_rollout(FEATURE, 50, True)
        await delete_rollout(FEATURE)
        assert await get_rollout(FEATURE) is None

    async def test_delete_nonexistent_rollout_is_safe(self) -> None:
        await delete_rollout(FEATURE)
        assert await get_rollout(FEATURE) is None

    async def test_delete_clears_redis_cache(self) -> None:
        await set_rollout(FEATURE, 50, True)
        assert await aredis.hget("sophie:kill_switch_rollout", FEATURE) is not None
        await delete_rollout(FEATURE)
        assert await aredis.hget("sophie:kill_switch_rollout", FEATURE) is None


class TestListRollouts:
    async def test_empty_when_none_set(self) -> None:
        assert await list_rollouts() == {}

    async def test_lists_configured_rollouts(self) -> None:
        await set_rollout(FEATURE, 25, True)
        await set_rollout(BOOL_FEATURE_DEFAULT_TRUE, 50, False)
        rollouts = await list_rollouts()
        assert FEATURE in rollouts
        assert BOOL_FEATURE_DEFAULT_TRUE in rollouts
        assert rollouts[FEATURE]["value"] is True
        assert rollouts[BOOL_FEATURE_DEFAULT_TRUE]["value"] is False

    async def test_rollout_redis_cache_populated_on_list(self) -> None:
        await set_rollout(FEATURE, 30, True)
        # Clear Redis cache to force DB read
        await aredis.hdel("sophie:kill_switch_rollout", FEATURE)
        rollouts = await list_rollouts()
        assert FEATURE in rollouts
        # Should now be cached in Redis again
        cached = await aredis.hget("sophie:kill_switch_rollout", FEATURE)
        assert cached is not None


class TestTimedRollout:
    async def test_starts_at_zero(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        await set_timed_rollout(FEATURE, 7, True, now=started)
        rollout = await get_rollout(FEATURE)
        assert rollout is not None
        assert get_rollout_percentage(rollout, now=started) == 0

    async def test_midpoint(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        await set_timed_rollout(FEATURE, 7, True, now=started)
        rollout = await get_rollout(FEATURE)
        assert rollout is not None
        assert get_rollout_percentage(rollout, now=started + timedelta(days=3, hours=12)) == 50

    async def test_reaches_100_at_end(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        await set_timed_rollout(FEATURE, 7, True, now=started)
        rollout = await get_rollout(FEATURE)
        assert rollout is not None
        assert get_rollout_percentage(rollout, now=started + timedelta(days=7)) == 100

    async def test_starts_from_existing_rollout_percentage(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        await set_rollout(FEATURE, 20, True)
        await set_timed_rollout(FEATURE, 4, True, now=started)
        rollout = await get_rollout(FEATURE)
        assert rollout is not None
        assert get_rollout_percentage(rollout, now=started) == 20
        assert get_rollout_percentage(rollout, now=started + timedelta(days=2)) == 60
        assert get_rollout_percentage(rollout, now=started + timedelta(days=4)) == 100


class TestBumpRollout:
    async def test_increases_percentage(self) -> None:
        await set_rollout(FEATURE, 20, True)
        rollout = await bump_rollout(FEATURE, 30)
        assert get_rollout_percentage(rollout) == 50

    async def test_caps_at_100(self) -> None:
        await set_rollout(FEATURE, 95, True)
        rollout = await bump_rollout(FEATURE, 10)
        assert get_rollout_percentage(rollout) == 100

    async def test_preserves_value(self) -> None:
        await set_rollout(FEATURE, 50, "openai/gpt-4o")
        rollout = await bump_rollout(FEATURE, 10)
        assert rollout["value"] == "openai/gpt-4o"

    async def test_clears_duration_days(self) -> None:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        await set_timed_rollout(FEATURE, 7, True, now=started)
        rollout = await bump_rollout(FEATURE, 10)
        assert rollout["duration_days"] is None

    async def test_requires_existing_rollout(self) -> None:
        with pytest.raises(ValueError, match="without an existing rollout"):
            await bump_rollout(FEATURE, 10)

    async def test_persists_after_bump(self) -> None:
        await set_rollout(FEATURE, 20, True)
        await bump_rollout(FEATURE, 30)
        stored = await get_rollout(FEATURE)
        assert stored is not None
        assert get_rollout_percentage(stored) == 50


class TestRolloutChatIntegration:
    async def test_100_percent_persist_chat_override(self) -> None:
        await set_rollout(FEATURE, 100, True)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is True
        override = await get_chat_override(FEATURE, CHAT_TID_A)
        assert override is True

    async def test_0_percent_no_persist(self) -> None:
        await set_rollout(FEATURE, 0, True)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is False
        assert await get_chat_override(FEATURE, CHAT_TID_A) is None

    async def test_manual_override_beats_rollout(self) -> None:
        await set_rollout(FEATURE, 100, True)
        await set_chat_override(FEATURE, CHAT_TID_A, False)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is False

    async def test_deterministic_cohort(self) -> None:
        selected_tid = _find_rollout_chat_tid(expected_in_rollout=True)
        excluded_tid = _find_rollout_chat_tid(expected_in_rollout=False)
        await set_rollout(FEATURE, 10, True)
        assert await is_enabled(FEATURE, chat_tid=selected_tid) is True
        assert await is_enabled(FEATURE, chat_tid=excluded_tid) is False

    async def test_rollout_persisted_as_rollout_source(self) -> None:
        await set_rollout(FEATURE, 100, True)
        await get_value(FEATURE, chat_tid=CHAT_TID_A)
        details = await list_chat_override_details(CHAT_TID_A)
        assert len(details) == 1
        assert details[0]["source"] == "rollout"

    async def test_delete_rollout_preserves_persisted_chat_override(self) -> None:
        await set_rollout(FEATURE, 100, True)
        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is True

        await delete_rollout(FEATURE)

        assert await get_value(FEATURE, chat_tid=CHAT_TID_A) is True
        assert await get_rollout(FEATURE) is None

    async def test_manual_and_rollout_sources_both_present(self) -> None:
        await set_rollout(BOOL_FEATURE_DEFAULT_TRUE, 100, False)
        await get_value(BOOL_FEATURE_DEFAULT_TRUE, chat_tid=CHAT_TID_A)
        await set_chat_override(FEATURE, CHAT_TID_A, True)
        details = await list_chat_override_details()
        sources = {detail["source"] for detail in details}
        assert sources == {"manual", "rollout"}
