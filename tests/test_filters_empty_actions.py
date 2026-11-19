from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler

from sophie_bot.modules.filters import enforce_middleware as module


@pytest.mark.parametrize("empty_handler", ["spam", "ai:spam"])
@pytest.mark.parametrize("has_valid_filter", [False, True])
async def test_empty_filters_do_not_match_or_block_valid_filters(
    empty_handler: str, has_valid_filter: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = SimpleNamespace(id="empty", handler=empty_handler, actions={}, silent=True, effective_version=2)
    valid = SimpleNamespace(
        id="valid", handler=empty_handler, actions={"reply": {"text": "hello"}}, silent=False, effective_version=2
    )
    monkeypatch.setattr(
        module.FiltersModel, "get_filters", AsyncMock(return_value=[empty, valid] if has_valid_filter else [empty])
    )
    match = AsyncMock(return_value=True)
    warning = Mock()
    monkeypatch.setattr(module, "match_filter_handler", match)
    monkeypatch.setattr(module.log, "debug", warning)
    middleware = module.EnforceFiltersMiddleware()
    actions = AsyncMock(return_value=(["reply"], []))
    monkeypatch.setattr(middleware, "_handle_filter_actions", actions)
    data = {
        "context": SimpleNamespace(event_chat=SimpleNamespace(iid="chat-iid"), user_in_group=None),
        "services": SimpleNamespace(redis=object()),
    }
    message = SimpleNamespace(chat=SimpleNamespace(id=-100123))
    if has_valid_filter:
        with pytest.raises(SkipHandler):
            await middleware._process_filters(message, data)
        match.assert_awaited_once()
        actions.assert_awaited_once()
        assert actions.await_args.args[0] is valid
        assert actions.await_args.args[2:] == (message, data)
    else:
        assert await middleware._process_filters(message, data) is None
        match.assert_not_awaited()
        actions.assert_not_awaited()
    warning.assert_called_once_with("Skipping filter without actions", filter_id="empty", chat_iid="chat-iid")
