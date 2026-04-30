from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Message

from sophie_bot.db.models.ai.ai_moderator import DetectionLevel
from sophie_bot.modules.ai.utils.ai_moderator import (
    CATEGORY_MIN_SCORES,
    CategoriesDict,
    check_moderator,
)


def _make_moderation_response(category_scores: dict[str, float]) -> SimpleNamespace:
    """Create a fake ModerationResponse with controlled category_scores."""
    return SimpleNamespace(
        results=[SimpleNamespace(category_scores=category_scores)],
    )


def _make_settings(**overrides: DetectionLevel) -> SimpleNamespace:
    """Create a fake AIModeratorModel settings object with DetectionLevel attributes."""
    defaults = {
        "sexual": DetectionLevel.NORMAL,
        "hate_and_discrimination": DetectionLevel.NORMAL,
        "violence_and_threats": DetectionLevel.NORMAL,
        "dangerous_and_criminal_content": DetectionLevel.NORMAL,
        "selfharm": DetectionLevel.NORMAL,
        "health": DetectionLevel.NORMAL,
        "financial": DetectionLevel.NORMAL,
        "law": DetectionLevel.NORMAL,
        "pii": DetectionLevel.NORMAL,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_message(text: str = "Hello world") -> AsyncMock:
    """Create a mock Message object."""
    message = AsyncMock(spec=Message)
    message.text = text
    return message


@pytest.fixture
def mock_history() -> AsyncMock:
    """Mock NewAIMessageHistory to avoid needing real message processing."""
    with patch("sophie_bot.modules.ai.utils.ai_moderator.NewAIMessageHistory") as mock_cls:
        instance = AsyncMock()
        instance.add_from_message = AsyncMock()
        instance.to_moderation = [{"role": "user", "content": "test message"}]
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def mock_convert() -> AsyncMock:
    """Mock convert_to_moderation_format."""
    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.convert_to_moderation_format",
        return_value=[{"role": "user", "content": "test message"}],
    ) as mock_fn:
        yield mock_fn


# --- check_moderator tests ---


@pytest.mark.asyncio
async def test_moderation_not_flagged(mock_history: AsyncMock, mock_convert: AsyncMock) -> None:
    """All scores below thresholds should result in not flagged."""
    low_scores = {key: threshold - 0.1 for key, threshold in CATEGORY_MIN_SCORES.items()}

    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.mistral_client.classifiers.moderate_chat_async",
        new=AsyncMock(return_value=_make_moderation_response(low_scores)),
    ):
        result = await check_moderator(_make_message())

    assert result.flagged is False
    assert result.categories.any_flagged is False


@pytest.mark.asyncio
async def test_moderation_flagged_sexual(mock_history: AsyncMock, mock_convert: AsyncMock) -> None:
    """Sexual score above threshold should flag the message."""
    scores = {key: 0.0 for key in CATEGORY_MIN_SCORES}
    scores["sexual"] = CATEGORY_MIN_SCORES["sexual"] + 0.1

    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.mistral_client.classifiers.moderate_chat_async",
        new=AsyncMock(return_value=_make_moderation_response(scores)),
    ):
        result = await check_moderator(_make_message())

    assert result.flagged is True
    assert result.categories.sexual is True
    assert result.categories.hate_and_discrimination is False


@pytest.mark.asyncio
async def test_moderation_flagged_multiple_categories(mock_history: AsyncMock, mock_convert: AsyncMock) -> None:
    """Multiple categories above thresholds should all be flagged."""
    scores = {key: 0.0 for key in CATEGORY_MIN_SCORES}
    scores["violence_and_threats"] = CATEGORY_MIN_SCORES["violence_and_threats"] + 0.1
    scores["hate_and_discrimination"] = CATEGORY_MIN_SCORES["hate_and_discrimination"] + 0.2
    scores["pii"] = CATEGORY_MIN_SCORES["pii"] + 0.3

    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.mistral_client.classifiers.moderate_chat_async",
        new=AsyncMock(return_value=_make_moderation_response(scores)),
    ):
        result = await check_moderator(_make_message())

    assert result.flagged is True
    assert result.categories.violence_and_threats is True
    assert result.categories.hate_and_discrimination is True
    assert result.categories.pii is True
    assert result.categories.sexual is False


@pytest.mark.asyncio
async def test_moderation_off_disables_category(mock_history: AsyncMock, mock_convert: AsyncMock) -> None:
    """Setting a category to OFF makes its threshold 1.1 (unreachable)."""
    scores = {key: 0.0 for key in CATEGORY_MIN_SCORES}
    # Set sexual score very high, but turn detection OFF
    scores["sexual"] = 1.0

    settings = _make_settings(sexual=DetectionLevel.OFF)

    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.mistral_client.classifiers.moderate_chat_async",
        new=AsyncMock(return_value=_make_moderation_response(scores)),
    ):
        result = await check_moderator(_make_message(), settings=settings)

    assert result.flagged is False
    assert result.categories.sexual is False


@pytest.mark.asyncio
async def test_moderation_high_lowers_threshold(mock_history: AsyncMock, mock_convert: AsyncMock) -> None:
    """HIGH detection level lowers threshold, catching lower scores."""
    # Use a score that is below NORMAL threshold but above HIGH threshold
    normal_threshold = CATEGORY_MIN_SCORES["sexual"]
    high_threshold = max(0.01, normal_threshold - 0.2)
    score_between = (normal_threshold + high_threshold) / 2

    scores = {key: 0.0 for key in CATEGORY_MIN_SCORES}
    scores["sexual"] = score_between

    settings = _make_settings(sexual=DetectionLevel.HIGH)

    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.mistral_client.classifiers.moderate_chat_async",
        new=AsyncMock(return_value=_make_moderation_response(scores)),
    ):
        result = await check_moderator(_make_message(), settings=settings)

    assert result.flagged is True
    assert result.categories.sexual is True


@pytest.mark.asyncio
async def test_moderation_low_raises_threshold(mock_history: AsyncMock, mock_convert: AsyncMock) -> None:
    """LOW detection level raises threshold, requiring higher scores to flag."""
    # Use a score that is above NORMAL threshold but below LOW threshold
    normal_threshold = CATEGORY_MIN_SCORES["sexual"]
    low_threshold = min(1.0, normal_threshold + 0.3)
    score_between = (normal_threshold + low_threshold) / 2

    scores = {key: 0.0 for key in CATEGORY_MIN_SCORES}
    scores["sexual"] = score_between

    settings = _make_settings(sexual=DetectionLevel.LOW)

    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.mistral_client.classifiers.moderate_chat_async",
        new=AsyncMock(return_value=_make_moderation_response(scores)),
    ):
        result = await check_moderator(_make_message(), settings=settings)

    assert result.flagged is False
    assert result.categories.sexual is False


@pytest.mark.asyncio
async def test_moderation_no_scores_not_flagged(mock_history: AsyncMock, mock_convert: AsyncMock) -> None:
    """When category_scores is empty/None, result should not be flagged."""
    response = SimpleNamespace(
        results=[SimpleNamespace(category_scores=None)],
    )

    with patch(
        "sophie_bot.modules.ai.utils.ai_moderator.mistral_client.classifiers.moderate_chat_async",
        new=AsyncMock(return_value=response),
    ):
        result = await check_moderator(_make_message())

    assert result.flagged is False
    assert result.categories == CategoriesDict()


# --- CategoriesDict tests ---


def test_categories_dict_any_flagged_true() -> None:
    """any_flagged returns True when at least one category is set."""
    categories = CategoriesDict(sexual=True)
    assert categories.any_flagged is True

    categories = CategoriesDict(pii=True, violence_and_threats=True)
    assert categories.any_flagged is True


def test_categories_dict_any_flagged_false() -> None:
    """any_flagged returns False when no categories are set."""
    categories = CategoriesDict()
    assert categories.any_flagged is False


def test_categories_dict_to_dict() -> None:
    """to_dict returns correct dictionary representation."""
    categories = CategoriesDict(
        sexual=True,
        hate_and_discrimination=False,
        violence_and_threats=True,
        dangerous_and_criminal_content=False,
        selfharm=False,
        health=False,
        financial=True,
        law=False,
        pii=False,
    )

    result = categories.to_dict()

    assert result == {
        "sexual": True,
        "hate_and_discrimination": False,
        "violence_and_threats": True,
        "dangerous_and_criminal_content": False,
        "selfharm": False,
        "health": False,
        "financial": True,
        "law": False,
        "pii": False,
    }
