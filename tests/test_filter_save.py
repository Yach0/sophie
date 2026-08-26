from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sophie_bot.db.models.filters import FilterHandlerType, FilterInSetupType
from sophie_bot.modules.filters.handlers.filter_save import FilterSaveHandler


@pytest.mark.asyncio
async def test_filter_save_rejects_filter_without_actions() -> None:
    event = MagicMock()
    event.answer = AsyncMock()
    filter_item = FilterInSetupType(handler=FilterHandlerType(keyword="spam"), actions={})
    handler = FilterSaveHandler(
        event,
        state=MagicMock(),
        connection=SimpleNamespace(),
    )

    with (
        patch.object(FilterInSetupType, "get_filter", AsyncMock(return_value=filter_item)),
        patch.object(handler, "save_filter", AsyncMock()) as save_filter,
    ):
        await handler.handle()

    event.answer.assert_awaited_once_with("No actions configured")
    save_filter.assert_not_awaited()
