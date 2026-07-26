from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Message
from openai.types.moderation import Moderation

from sophie_bot.modules.ai.utils.message_history import convert_to_openai_moderation_format
from sophie_bot.modules.ai.utils.moderation import ModerationCategory, check_moderator
from sophie_bot.modules.ai.utils.moderation.providers.openai import OpenAIModerationProvider
from sophie_bot.utils.feature_flags import set_value

_DEFAULTS = {native.key: native.default_threshold for native in OpenAIModerationProvider.native_categories}


def _make_message(text: str = "Hello world") -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.text = text
    return message


def _openai_returning(scores: dict[str, float | None] | None):
    """Patch the moderations endpoint with a response whose category_scores dumps to `scores`."""
    results = []
    if scores is not None:
        category_scores = SimpleNamespace(model_dump=lambda by_alias=False: dict(scores))
        results = [SimpleNamespace(category_scores=category_scores)]

    client = SimpleNamespace(
        moderations=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(results=results)))
    )
    return patch(
        "sophie_bot.modules.ai.utils.moderation.providers.openai.get_openai_client",
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
async def openai_provider() -> None:
    await set_value("ai_moderation_provider", "openai")
    yield
    await set_value("ai_moderation_provider", "mistral")


pytestmark = pytest.mark.usefixtures("db_init", "mock_history", "openai_provider")


def test_native_keys_match_the_openai_sdk() -> None:
    """The whole provider hinges on model_dump(by_alias=True) keys matching NativeCategory.key."""
    category_scores_cls = Moderation.model_fields["category_scores"].annotation
    sdk_keys = {field.alias or name for name, field in category_scores_cls.model_fields.items()}

    assert {native.key for native in OpenAIModerationProvider.native_categories} == sdk_keys


def test_convert_to_openai_moderation_format_drops_roles_and_empties() -> None:
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": ""},
        {"role": "system", "content": "second"},
    ]

    assert convert_to_openai_moderation_format(messages) == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]


async def test_native_categories_fold_into_sophie_categories() -> None:
    scores = dict.fromkeys(_DEFAULTS, 0.0)
    scores["sexual/minors"] = _DEFAULTS["sexual/minors"] + 0.1

    with _openai_returning(scores):
        result = await check_moderator(_make_message())

    # A narrow native category still surfaces as its broad Sophie category.
    assert result.triggered == frozenset({ModerationCategory.SEXUAL})
    assert result.triggered_native == frozenset({"sexual/minors"})


async def test_narrow_category_has_its_own_threshold() -> None:
    # 0.3 clears sexual/minors (0.2) but not sexual (0.5): the two must not share a cut-off.
    scores = dict.fromkeys(_DEFAULTS, 0.0)
    scores["sexual"] = 0.3
    scores["sexual/minors"] = 0.3

    with _openai_returning(scores):
        result = await check_moderator(_make_message())

    assert result.triggered_native == frozenset({"sexual/minors"})


async def test_several_natives_fold_into_one_category() -> None:
    scores = dict.fromkeys(_DEFAULTS, 0.0)
    scores["hate"] = 0.9
    scores["harassment"] = 0.9

    with _openai_returning(scores):
        result = await check_moderator(_make_message())

    assert result.triggered == frozenset({ModerationCategory.HATE_AND_DISCRIMINATION})
    assert result.triggered_native == frozenset({"hate", "harassment"})


async def test_none_scores_are_treated_as_zero() -> None:
    scores: dict[str, float | None] = dict.fromkeys(_DEFAULTS, 0.0)
    scores["illicit"] = None
    scores["illicit/violent"] = None
    scores["hate"] = 0.9

    with _openai_returning(scores):
        result = await check_moderator(_make_message())

    assert "illicit" not in result.scores
    assert result.triggered == frozenset({ModerationCategory.HATE_AND_DISCRIMINATION})


async def test_categories_without_openai_equivalent_never_fire() -> None:
    scores = dict.fromkeys(_DEFAULTS, 0.99)

    with _openai_returning(scores):
        result = await check_moderator(_make_message())

    assert ModerationCategory.PII not in result.triggered
    assert ModerationCategory.HEALTH not in result.triggered
    assert ModerationCategory.FINANCIAL not in result.triggered
    assert ModerationCategory.LAW not in result.triggered


async def test_empty_results_not_flagged() -> None:
    with _openai_returning(None):
        result = await check_moderator(_make_message())

    assert result.flagged is False


async def test_threshold_flag_overrides_openai_default() -> None:
    scores = dict.fromkeys(_DEFAULTS, 0.0)
    scores["violence"] = 0.1

    with _openai_returning(scores):
        assert (await check_moderator(_make_message())).flagged is False

        await set_value("ai_moderation_threshold_openai_violence", 0.05)
        try:
            result = await check_moderator(_make_message())
        finally:
            await set_value("ai_moderation_threshold_openai_violence", _DEFAULTS["violence"])

    assert result.triggered == frozenset({ModerationCategory.VIOLENCE_AND_THREATS})
