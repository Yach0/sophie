from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, Chat, Message, User

from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override, set_enabled

PRIVATE_CHAT_ID = 42


def _pm_message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=PRIVATE_CHAT_ID, type="private"),
        from_user=User(id=PRIVATE_CHAT_ID, is_bot=False, first_name="User"),
        text="/lock url",
    )


@pytest.fixture(autouse=True)
async def _reset_feature_flag_overrides(db_init: object) -> AsyncGenerator[None]:
    collection = FeatureFlagOverride.get_pymongo_collection()
    query = {"feature": "locks", "chat_tid": {"$in": [None, PRIVATE_CHAT_ID]}}
    previous_overrides = await collection.find(query).to_list()
    await collection.delete_many(query)
    try:
        yield
    finally:
        await collection.delete_many(query)
        if previous_overrides:
            await collection.insert_many(previous_overrides)


@pytest.mark.asyncio
async def test_pm_override_blocks_enabled_flag_without_a_connection(test_services: ApplicationServices) -> None:
    await set_enabled("locks", True, redis=test_services.redis)
    await set_chat_override("locks", PRIVATE_CHAT_ID, False, redis=test_services.redis)
    context = RequestContext()

    assert await FeatureFlagFilter("locks")(_pm_message(), test_services, context) is False
    assert await FeatureFlagFilter("locks", enabled=False)(_pm_message(), test_services, context) is True


@pytest.mark.asyncio
async def test_inline_callback_without_a_message_is_not_enabled(test_services: ApplicationServices) -> None:
    await set_enabled("locks", True, redis=test_services.redis)
    callback = CallbackQuery(
        id="inline-callback",
        from_user=User(id=PRIVATE_CHAT_ID, is_bot=False, first_name="User"),
        chat_instance="instance",
        inline_message_id="inline-message",
    )

    assert await FeatureFlagFilter("locks")(callback, test_services, RequestContext()) is False


@pytest.mark.asyncio
async def test_group_or_connected_filter_answers_disconnected_callback_safely() -> None:
    message = _pm_message()
    callback = CallbackQuery(
        id="callback",
        from_user=User(id=PRIVATE_CHAT_ID, is_bot=False, first_name="User"),
        chat_instance="instance",
        message=message,
    )
    answer = AsyncMock()

    with patch.object(CallbackQuery, "answer", answer), pytest.raises(SkipHandler):
        await GroupOrConnectedFilter()(
            callback,
            context=RequestContext(),
            event_chat=message.chat,
        )

    answer.assert_awaited_once()
