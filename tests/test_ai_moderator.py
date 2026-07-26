from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from sophie_bot.db.models.ai.ai_moderator import DetectionLevel
from sophie_bot.modules.ai.callbacks import AIModeratorCategoryCallback
from sophie_bot.modules.ai.handlers.aimoderator import _build_doc, _build_keyboard
from sophie_bot.modules.ai.middlewares.ai_moderator import AiModeratorMiddleware
from sophie_bot.modules.ai.utils.moderation import (
    MODERATION_CATEGORIES_TRANSLATES,
    ModerationCategory,
    check_moderator,
    get_moderation_provider,
)
from sophie_bot.modules.ai.utils.moderation.providers.mistral import MistralModerationProvider
from sophie_bot.modules.ai.utils.moderation.settings import LEVEL_CYCLE, next_level
from sophie_bot.modules.ai.utils.moderation.thresholds import resolve_level_multipliers, resolve_thresholds
from sophie_bot.utils.feature_flags import set_chat_override, set_value

_MISTRAL_DEFAULTS = {native.key: native.default_threshold for native in MistralModerationProvider.native_categories}
_CHAT_TID = -1001234567890


def _make_moderation_response(category_scores: dict[str, float] | None) -> SimpleNamespace:
    return SimpleNamespace(results=[SimpleNamespace(category_scores=category_scores)])


def _make_settings(**overrides: DetectionLevel) -> SimpleNamespace:
    defaults = {category.value: DetectionLevel.NORMAL for category in ModerationCategory}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_message(text: str = "Hello world") -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.text = text
    return message


def _mistral_returning(scores: dict[str, float] | None):
    """Stand in for the catalog-built Mistral client, whose key now lives in the database."""
    client = SimpleNamespace(
        classifiers=SimpleNamespace(moderate_chat_async=AsyncMock(return_value=_make_moderation_response(scores)))
    )
    return patch(
        "sophie_bot.modules.ai.utils.moderation.providers.mistral.get_mistral_client",
        new=AsyncMock(return_value=client),
    )


@pytest.fixture
def mock_history() -> AsyncMock:
    with patch("sophie_bot.modules.ai.utils.moderation.AIMessageHistory") as mock_cls:
        instance = AsyncMock()
        instance.add_from_message = AsyncMock()
        instance.to_moderation = [{"role": "user", "content": "test message"}]
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def mock_convert() -> AsyncMock:
    with patch(
        "sophie_bot.modules.ai.utils.moderation.providers.mistral.convert_to_moderation_format",
        return_value=[{"role": "user", "content": "test message"}],
    ) as mock_fn:
        yield mock_fn


pytestmark = pytest.mark.usefixtures("db_init", "mock_history", "mock_convert")


# --- check_moderator ---


async def test_moderation_not_flagged() -> None:
    scores = {key: threshold - 0.1 for key, threshold in _MISTRAL_DEFAULTS.items()}

    with _mistral_returning(scores):
        result = await check_moderator(_make_message())

    assert result.flagged is False
    assert result.triggered == frozenset()


async def test_moderation_flagged_sexual() -> None:
    scores = dict.fromkeys(_MISTRAL_DEFAULTS, 0.0)
    scores["sexual"] = _MISTRAL_DEFAULTS["sexual"] + 0.1

    with _mistral_returning(scores):
        result = await check_moderator(_make_message())

    assert result.flagged is True
    assert result.triggered == frozenset({ModerationCategory.SEXUAL})
    assert result.triggered_native == frozenset({"sexual"})


async def test_moderation_flagged_multiple_categories() -> None:
    scores = dict.fromkeys(_MISTRAL_DEFAULTS, 0.0)
    scores["violence_and_threats"] = _MISTRAL_DEFAULTS["violence_and_threats"] + 0.1
    scores["hate_and_discrimination"] = _MISTRAL_DEFAULTS["hate_and_discrimination"] + 0.2
    scores["pii"] = _MISTRAL_DEFAULTS["pii"] + 0.3

    with _mistral_returning(scores):
        result = await check_moderator(_make_message())

    assert result.triggered == frozenset(
        {
            ModerationCategory.VIOLENCE_AND_THREATS,
            ModerationCategory.HATE_AND_DISCRIMINATION,
            ModerationCategory.PII,
        }
    )


async def test_moderation_no_scores_not_flagged() -> None:
    with _mistral_returning(None):
        result = await check_moderator(_make_message())

    assert result.flagged is False
    assert result.scores == {}


# --- DetectionLevel ---


async def test_moderation_off_disables_category() -> None:
    scores = dict.fromkeys(_MISTRAL_DEFAULTS, 0.0)
    scores["sexual"] = 1.0

    with _mistral_returning(scores):
        result = await check_moderator(_make_message(), settings=_make_settings(sexual=DetectionLevel.OFF))

    assert result.flagged is False


async def test_moderation_high_lowers_threshold() -> None:
    normal = _MISTRAL_DEFAULTS["sexual"]
    scores = dict.fromkeys(_MISTRAL_DEFAULTS, 0.0)
    scores["sexual"] = normal - 0.1

    with _mistral_returning(scores):
        result = await check_moderator(_make_message(), settings=_make_settings(sexual=DetectionLevel.HIGH))

    assert result.triggered == frozenset({ModerationCategory.SEXUAL})


async def test_moderation_low_raises_threshold() -> None:
    normal = _MISTRAL_DEFAULTS["sexual"]
    scores = dict.fromkeys(_MISTRAL_DEFAULTS, 0.0)
    scores["sexual"] = normal + 0.1

    with _mistral_returning(scores):
        result = await check_moderator(_make_message(), settings=_make_settings(sexual=DetectionLevel.LOW))

    assert result.flagged is False


# --- feature flags ---


async def test_thresholds_come_from_flags() -> None:
    provider = MistralModerationProvider()

    thresholds = await resolve_thresholds(provider, None)
    assert thresholds["sexual"] == _MISTRAL_DEFAULTS["sexual"]

    await set_value("ai_moderation_threshold_mistral_sexual", 0.05)
    try:
        thresholds = await resolve_thresholds(provider, None)
        assert thresholds["sexual"] == pytest.approx(0.05)
    finally:
        await set_value("ai_moderation_threshold_mistral_sexual", _MISTRAL_DEFAULTS["sexual"])


async def test_threshold_chat_override_flags_low_score() -> None:
    scores = dict.fromkeys(_MISTRAL_DEFAULTS, 0.0)
    scores["sexual"] = 0.1

    with _mistral_returning(scores):
        assert (await check_moderator(_make_message(), chat_tid=_CHAT_TID)).flagged is False

        await set_chat_override("ai_moderation_threshold_mistral_sexual", _CHAT_TID, 0.05)
        result = await check_moderator(_make_message(), chat_tid=_CHAT_TID)

    assert result.triggered == frozenset({ModerationCategory.SEXUAL})


async def test_level_multipliers_come_from_flags() -> None:
    multipliers = await resolve_level_multipliers(None)
    assert multipliers == {DetectionLevel.LOW: 0.7, DetectionLevel.NORMAL: 1.0, DetectionLevel.HIGH: 1.3}

    await set_value("ai_moderation_level_high_multiplier", 3.0)
    try:
        assert (await resolve_level_multipliers(None))[DetectionLevel.HIGH] == pytest.approx(3.0)
    finally:
        await set_value("ai_moderation_level_high_multiplier", 1.3)


async def test_level_multiplier_scales_the_score() -> None:
    # 0.3 misses the 0.5 threshold even at HIGH's default 1.3x, and clears it once HIGH scales by 3.
    scores = dict.fromkeys(_MISTRAL_DEFAULTS, 0.0)
    scores["sexual"] = 0.3
    settings = _make_settings(sexual=DetectionLevel.HIGH)

    with _mistral_returning(scores):
        assert (await check_moderator(_make_message(), settings=settings)).flagged is False

        await set_value("ai_moderation_level_high_multiplier", 3.0)
        try:
            result = await check_moderator(_make_message(), settings=settings)
        finally:
            await set_value("ai_moderation_level_high_multiplier", 1.3)

    assert result.triggered == frozenset({ModerationCategory.SEXUAL})


async def test_provider_flag_selects_backend() -> None:
    assert (await get_moderation_provider()).name == "mistral"

    await set_value("ai_moderation_provider", "openai")
    try:
        assert (await get_moderation_provider()).name == "openai"
    finally:
        await set_value("ai_moderation_provider", "mistral")


async def test_unknown_provider_falls_back_to_mistral() -> None:
    await set_value("ai_moderation_provider", "nonsense")
    try:
        assert (await get_moderation_provider()).name == "mistral"
    finally:
        await set_value("ai_moderation_provider", "mistral")


# --- the /aimoderator picker ---


def test_level_cycle_walks_every_level_and_returns() -> None:
    level = DetectionLevel.OFF
    walked = [level]
    for _step in LEVEL_CYCLE:
        level = next_level(level)
        walked.append(level)

    assert walked[:-1] == list(LEVEL_CYCLE)
    assert walked[-1] == DetectionLevel.OFF


def test_keyboard_shows_every_category_with_its_level() -> None:
    levels = dict.fromkeys(ModerationCategory, DetectionLevel.NORMAL)
    levels[ModerationCategory.PII] = DetectionLevel.OFF

    buttons = [button for row in _build_keyboard(levels).inline_keyboard for button in row]

    assert len(buttons) == len(ModerationCategory)
    pii_button = next(button for button in buttons if "Personal data" in button.text)
    assert "Off" in pii_button.text
    assert AIModeratorCategoryCallback.unpack(pii_button.callback_data).category == ModerationCategory.PII.value


def test_picker_table_describes_every_category() -> None:
    rendered = _build_doc().to_rich()

    for category in ModerationCategory:
        assert str(MODERATION_CATEGORIES_TRANSLATES[category]) in rendered


# --- the deletion notice ---


def _notice_message() -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.chat = SimpleNamespace(id=_CHAT_TID)
    message.message_thread_id = None
    # aiogram's Message.delete() is sync and returns an awaitable method object, so spec'ing gives
    # a MagicMock that common_try cannot await.
    message.delete = AsyncMock()
    message.from_user = SimpleNamespace(id=42, first_name="Spammer")
    return message


async def _send_notice() -> tuple[str, AsyncMock]:
    schedule_mock = MagicMock()
    send_mock = AsyncMock(return_value=SimpleNamespace(message_id=777))

    with (
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.bot.send_message", send_mock),
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.schedule_message_deletion", schedule_mock),
    ):
        await AiModeratorMiddleware._triggered(
            _notice_message(), frozenset({ModerationCategory.SEXUAL}), _CHAT_TID
        )

    return send_mock.call_args.kwargs["text"], schedule_mock


async def test_notice_announces_and_schedules_its_own_deletion() -> None:
    text, schedule_mock = await _send_notice()

    assert "deleted shortly" in text
    schedule_mock.assert_called_once_with(_CHAT_TID, [777], delay_seconds=30)


async def test_notice_stays_when_delay_flag_is_zero() -> None:
    await set_value("ai_moderation_notice_delete_after_seconds", 0)
    try:
        text, schedule_mock = await _send_notice()
    finally:
        await set_value("ai_moderation_notice_delete_after_seconds", 30)

    assert "deleted shortly" not in text
    schedule_mock.assert_not_called()
