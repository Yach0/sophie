from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message

from sophie_bot.constants import AI_FILTER_DAILY_LIMIT_PER_CHAT, AI_FILTER_NEW_USER_MAX_AGE_HOURS
from sophie_bot.modules.filters.enforce_middleware import EnforceFiltersMiddleware
from sophie_bot.modules.filters.utils_.handle_action import get_effective_filter_actions
from sophie_bot.modules.filters.utils_.match_handler import consume_ai_filter_daily_quota, match_ai_handler


@pytest.mark.asyncio
async def test_match_ai_handler_skips_users_older_than_threshold(
    test_redis: object,
) -> None:
    message = AsyncMock(spec=Message)
    user_in_group = SimpleNamespace(
        first_saw=datetime.now(UTC) - timedelta(hours=AI_FILTER_NEW_USER_MAX_AGE_HOURS + 1)
    )

    with (
        patch("sophie_bot.modules.filters.utils_.match_handler.is_enabled", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.filters.utils_.match_handler.extract_message_content", AsyncMock()) as extract_mock,
        patch("sophie_bot.modules.filters.utils_.match_handler.run_structured_task", AsyncMock()) as ai_mock,
    ):
        matched = await match_ai_handler(
            message,
            "spam",
            user_in_group=user_in_group,
            services=SimpleNamespace(redis=test_redis),
        )

    assert matched is False
    extract_mock.assert_not_awaited()
    ai_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_consume_ai_filter_daily_quota_enforces_chat_limit(
    test_redis: object,
) -> None:
    chat_tid = -100987654321

    async def get_value(
        feature: str,
        chat_tid: int | None = None,
        **_kwargs: object,
    ) -> int:
        _ = chat_tid
        return (
            AI_FILTER_DAILY_LIMIT_PER_CHAT
            if feature == "ai_filter_daily_chat_limit"
            else 100
        )

    with patch("sophie_bot.modules.filters.utils_.match_handler.get_value", get_value):
        for current_attempt in range(AI_FILTER_DAILY_LIMIT_PER_CHAT):
            assert (
                await consume_ai_filter_daily_quota(
                    chat_tid,
                    services=SimpleNamespace(redis=test_redis),
                )
                is True
            ), current_attempt

        assert (
            await consume_ai_filter_daily_quota(
                chat_tid,
                services=SimpleNamespace(redis=test_redis),
            )
            is False
        )

    redis_keys = await test_redis.keys("ai_filter_daily_limit:*")
    assert redis_keys


@pytest.mark.asyncio
async def test_consume_ai_filter_daily_quota_enforces_user_limit(
    test_redis: object,
) -> None:
    chat_tid = -100987654322
    user_tid = 123456

    async def get_value(
        feature: str,
        chat_tid: int | None = None,
        **_kwargs: object,
    ) -> int:
        _ = chat_tid
        return 100 if feature == "ai_filter_daily_chat_limit" else 2

    with patch("sophie_bot.modules.filters.utils_.match_handler.get_value", get_value):
        services = SimpleNamespace(redis=test_redis)
        assert await consume_ai_filter_daily_quota(
            chat_tid,
            user_tid=user_tid,
            services=services,
        ) is True
        assert await consume_ai_filter_daily_quota(
            chat_tid,
            user_tid=user_tid,
            services=services,
        ) is True
        assert await consume_ai_filter_daily_quota(
            chat_tid,
            user_tid=user_tid,
            services=services,
        ) is False


@pytest.mark.asyncio
async def test_match_ai_handler_skips_after_new_user_message_limit(
    test_redis: object,
) -> None:
    message = AsyncMock(spec=Message)
    message.chat = SimpleNamespace(id=-100123)
    message.from_user = SimpleNamespace(id=123)
    user_in_group = SimpleNamespace(
        first_saw=datetime.now(UTC),
        ai_filter_seen_messages=10,
        save=AsyncMock(),
    )

    async def get_value(
        feature: str,
        chat_tid: int | None = None,
        **_kwargs: object,
    ) -> int:
        _ = chat_tid
        return (
            10
            if feature == "ai_filter_new_user_message_limit"
            else 100
        )

    with (
        patch("sophie_bot.modules.filters.utils_.match_handler.is_enabled", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.filters.utils_.match_handler.get_value", get_value),
        patch("sophie_bot.modules.filters.utils_.match_handler.extract_message_content", AsyncMock()) as extract_mock,
        patch("sophie_bot.modules.filters.utils_.match_handler.run_structured_task", AsyncMock()) as ai_mock,
    ):
        matched = await match_ai_handler(
            message,
            "spam",
            user_in_group=user_in_group,
            services=SimpleNamespace(redis=test_redis),
        )

    assert matched is False
    user_in_group.save.assert_not_awaited()
    extract_mock.assert_not_awaited()
    ai_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_enforce_filters_evaluates_only_one_ai_filter_per_message(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = EnforceFiltersMiddleware()
    message = AsyncMock(spec=Message)
    message.chat = SimpleNamespace(id=-100123)

    chat_db = SimpleNamespace(iid="chat-iid")
    filters = [
        SimpleNamespace(handler="ai:first prompt", effective_version=1, silent=False),
        SimpleNamespace(handler="ai:second prompt", effective_version=1, silent=False),
    ]

    monkeypatch.setattr(
        "sophie_bot.modules.filters.enforce_middleware.FiltersModel.get_filters", AsyncMock(return_value=filters)
    )
    match_mock = AsyncMock(return_value=False)
    monkeypatch.setattr("sophie_bot.modules.filters.enforce_middleware.match_filter_handler", match_mock)

    await middleware._process_filters(
        message,
        {
            "context": SimpleNamespace(
                event_chat=chat_db,
                user_in_group=None,
            ),
            "services": SimpleNamespace(),
        },
    )

    assert match_mock.await_count == 1


@pytest.mark.asyncio
async def test_enforce_filters_skips_ai_when_deterministic_filter_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = EnforceFiltersMiddleware()
    message = AsyncMock(spec=Message)
    message.chat = SimpleNamespace(id=-100124)

    chat_db = SimpleNamespace(iid="chat-iid")
    filters = [
        SimpleNamespace(handler="ai:first prompt", effective_version=1, silent=False),
        SimpleNamespace(handler="exact:spam", effective_version=1, silent=False),
    ]

    monkeypatch.setattr(
        "sophie_bot.modules.filters.enforce_middleware.FiltersModel.get_filters", AsyncMock(return_value=filters)
    )
    match_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("sophie_bot.modules.filters.enforce_middleware.match_filter_handler", match_mock)
    monkeypatch.setattr(middleware, "_process_filter", AsyncMock(return_value=([], [])))

    with pytest.raises(SkipHandler):
        await middleware._process_filters(
            message,
            {
                "context": SimpleNamespace(
                    event_chat=chat_db,
                    user_in_group=None,
                ),
                "services": SimpleNamespace(),
            },
        )

    assert match_mock.await_count == 1
    assert match_mock.await_args.args[1] == "exact:spam"


def test_get_effective_filter_actions_returns_modern_actions() -> None:
    filter_item = SimpleNamespace(action="legacy", actions={"modern": {"enabled": True}})

    actions = get_effective_filter_actions(filter_item)

    assert len(actions) == 1
    assert actions[0].name == "modern"
    assert actions[0].data == {"enabled": True}


def test_get_effective_filter_actions_returns_empty_without_actions() -> None:
    filter_item = SimpleNamespace(action="legacy", actions={})

    actions = get_effective_filter_actions(filter_item)

    assert actions == []
