from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
from pydantic import BaseModel

from sophie_bot.modules.filters.api.actions import list_filter_actions
from sophie_bot.modules.filters.api.filters import create_filter, delete_filter, update_filter
from sophie_bot.modules.filters.api.schemas import FilterActionPayload, FilterCreate, FilterUpdate
from sophie_bot.modules.filters.api.utils import build_filter_response


class DummyActionData(BaseModel):
    label: str = "default"


class DummyAction:
    name = "dummy"
    icon = "D"
    title = "Dummy"
    as_filter = True
    as_button = False
    as_flood = False
    allow_warns = True
    interactive_setup = None
    data_object = DummyActionData
    default_data = DummyActionData()

    @staticmethod
    def description(data: DummyActionData | None) -> str:
        return f"Run {data.label if data else 'default'}"


class NotFilterAction(DummyAction):
    name = "not_filter"
    as_filter = False


@pytest.mark.asyncio
async def test_create_filter_creates_modern_filter() -> None:
    chat_iid = PydanticObjectId()
    user = MagicMock()
    user.tid = 99
    chat = MagicMock()
    chat.iid = chat_iid
    chat.tid = -100123

    payload = FilterCreate(handler="spam", actions=[FilterActionPayload(name="dummy", data={})])
    filter_item = MagicMock()
    filter_item.id = PydanticObjectId()
    filter_item.handler = "spam"
    filter_item.action = None
    filter_item.actions = {"dummy": {"label": "default"}}
    filter_item.time = None
    filter_item.effective_version = 2
    filter_item.insert = AsyncMock()

    with (
        patch("sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid", new=AsyncMock(return_value=chat)),
        patch("sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword", new=AsyncMock(return_value=None)),
        patch("sophie_bot.modules.filters.api.utils.ALL_MODERN_ACTIONS", {"dummy": DummyAction()}),
        patch("sophie_bot.modules.filters.api.filters.FiltersModel", return_value=filter_item),
        patch("sophie_bot.modules.filters.api.filters.log_event", new=AsyncMock()),
    ):
        response = await create_filter(chat_iid, payload, user)

    assert response.handler == "spam"
    assert response.version == 2
    assert response.is_legacy is False
    assert response.actions[0].name == "dummy"
    assert response.actions[0].data == {"label": "default"}


@pytest.mark.asyncio
async def test_create_filter_rejects_non_filter_action() -> None:
    chat_iid = PydanticObjectId()
    user = MagicMock()
    chat = MagicMock()
    chat.iid = chat_iid

    payload = FilterCreate(handler="spam", actions=[FilterActionPayload(name="not_filter", data={})])

    with (
        patch("sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid", new=AsyncMock(return_value=chat)),
        patch("sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword", new=AsyncMock(return_value=None)),
        patch("sophie_bot.modules.filters.api.utils.ALL_MODERN_ACTIONS", {"not_filter": NotFilterAction()}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_filter(chat_iid, payload, user)

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_update_filter_converts_legacy_filter_to_modern() -> None:
    chat_iid = PydanticObjectId()
    filter_id = PydanticObjectId()
    user = MagicMock()
    user.tid = 88
    chat = MagicMock()
    chat.iid = chat_iid
    chat.tid = -100456

    filter_item = MagicMock()
    filter_item.id = filter_id
    filter_item.chat.id = chat_iid
    filter_item.handler = "old"
    filter_item.version = 1
    filter_item.action = "reply_message"
    filter_item.actions = {}
    filter_item.time = None
    filter_item.effective_version = 1
    filter_item.save = AsyncMock()

    payload = FilterUpdate(handler="updated", actions=[FilterActionPayload(name="dummy", data={"label": "done"})])

    with (
        patch("sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid", new=AsyncMock(return_value=chat)),
        patch("sophie_bot.modules.filters.api.filters.FiltersModel.get_by_id", new=AsyncMock(return_value=filter_item)),
        patch("sophie_bot.modules.filters.api.utils.FiltersModel.get_by_id", new=AsyncMock(return_value=filter_item)),
        patch("sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword", new=AsyncMock(return_value=None)),
        patch("sophie_bot.modules.filters.api.utils.ALL_MODERN_ACTIONS", {"dummy": DummyAction()}),
        patch("sophie_bot.modules.filters.api.filters.log_event", new=AsyncMock()),
    ):
        response = await update_filter(chat_iid, filter_id, payload, user)

    assert filter_item.handler == "updated"
    assert filter_item.version == 2
    assert filter_item.action is None
    assert filter_item.actions == {"dummy": {"label": "done"}}
    assert response.actions[0].description == "Run done"


def test_build_filter_response_exposes_compatibility_action_as_effective_action() -> None:
    filter_item = MagicMock()
    filter_item.id = PydanticObjectId()
    filter_item.handler = "old"
    filter_item.action = "legacy"
    filter_item.actions = {}
    filter_item.time = None
    filter_item.effective_version = 1

    with patch(
        "sophie_bot.modules.filters.api.utils.LEGACY_FILTERS_ACTIONS",
        {"legacy": {"title": "Legacy", "handle": None, "action": None, "del_btn_name": None, "setup": None}},
    ):
        response = build_filter_response(filter_item)

    assert response.is_legacy is True
    assert response.legacy_action == "legacy"
    assert response.actions[0].name == "legacy"
    assert response.actions[0].title == "Legacy"


@pytest.mark.asyncio
async def test_delete_filter_removes_existing_filter() -> None:
    chat_iid = PydanticObjectId()
    filter_id = PydanticObjectId()
    user = MagicMock()
    user.tid = 77
    chat = MagicMock()
    chat.iid = chat_iid
    chat.tid = -100789

    filter_item = MagicMock()
    filter_item.chat.id = chat_iid
    filter_item.handler = "spam"
    filter_item.delete = AsyncMock()

    with (
        patch("sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid", new=AsyncMock(return_value=chat)),
        patch("sophie_bot.modules.filters.api.filters.FiltersModel.get_by_id", new=AsyncMock(return_value=filter_item)),
        patch("sophie_bot.modules.filters.api.filters.log_event", new=AsyncMock()),
    ):
        response = await delete_filter(chat_iid, filter_id, user)

    assert response.status_code == 204
    filter_item.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_filter_actions_returns_catalog() -> None:
    user = SimpleNamespace(tid=1)

    with (
        patch("sophie_bot.modules.filters.api.utils.ALL_MODERN_ACTIONS", {"dummy": DummyAction()}),
        patch(
            "sophie_bot.modules.filters.api.actions.LEGACY_FILTERS_ACTIONS",
            {"legacy": {"title": "Legacy", "handle": None, "action": None, "del_btn_name": None, "setup": None}},
        ),
    ):
        response = await list_filter_actions(user)

    assert response.limits.max_ai_filters_per_chat >= 1
    assert response.actions[0].name == "dummy"
    assert response.actions[0].default_data == {"label": "default"}
    assert response.legacy_actions[0].name == "legacy"
