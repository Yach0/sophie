from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message
from beanie import PydanticObjectId
from fastapi import HTTPException
from pydantic import BaseModel

from sophie_bot.modules.filters.api.actions import list_filter_actions
from sophie_bot.modules.filters.api.filters import (
    create_filter,
    delete_filter,
    update_filter,
)
from sophie_bot.modules.filters.api.schemas import (
    FilterActionPayload,
    FilterCreate,
    FilterUpdate,
)
from sophie_bot.modules.filters.api.utils import build_filter_response, validate_filter_handler
from sophie_bot.shared.actions import ActionDefinition, ModernActionABC


class DummyActionData(BaseModel):
    label: str = "default"


class DummyAction(ModernActionABC[DummyActionData]):
    definition = ActionDefinition(
        name="dummy",
        icon="D",
        title="Dummy",
        data_object=DummyActionData,
        default_data=DummyActionData(),
    )

    @staticmethod
    def description(data: DummyActionData) -> str:
        return f"Run {data.label}"

    async def handle(
        self,
        message: Message,
        data: dict[str, object],
        filter_data: DummyActionData,
    ) -> None:
        return None


class NotFilterAction(DummyAction):
    definition = ActionDefinition(
        name="not_filter",
        icon="D",
        title="Dummy",
        data_object=DummyActionData,
        default_data=DummyActionData(),
        as_filter=False,
    )


DUMMY_HANDLER = DummyAction()
DUMMY_ACTIONS = {"dummy": DUMMY_HANDLER.definition}
DUMMY_HANDLERS = {"dummy": DUMMY_HANDLER}


def _services(
    actions: dict[str, ActionDefinition[DummyActionData]] | None = None,
    handlers: dict[str, ModernActionABC[DummyActionData]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        modules=SimpleNamespace(
            actions=actions if actions is not None else DUMMY_ACTIONS,
            action_handlers=handlers if handlers is not None else DUMMY_HANDLERS,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["spam", "re:.*", "re:^never-matches-this-random-text$"])
async def test_create_filter_creates_modern_filter(handler: str) -> None:
    chat_iid = PydanticObjectId()
    user = MagicMock(tid=99)
    chat = MagicMock(iid=chat_iid, tid=-100123)
    payload = FilterCreate(
        handler=handler,
        actions=[FilterActionPayload(name="dummy", data={})],
    )
    filter_item = MagicMock(
        id=PydanticObjectId(),
        handler=handler,
        action=None,
        actions={"dummy": {"label": "default"}},
        time=None,
        effective_version=2,
        insert=AsyncMock(),
    )

    with (
        patch(
            "sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid",
            new=AsyncMock(return_value=chat),
        ),
        patch(
            "sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "sophie_bot.modules.filters.api.filters.FiltersModel",
            return_value=filter_item,
        ),
        patch(
            "sophie_bot.modules.filters.api.filters.log_event",
            new=AsyncMock(),
        ),
    ):
        response = await create_filter(chat_iid, payload, user, _services())

    assert response.handler == handler
    assert response.version == 2
    assert response.actions[0].name == "dummy"
    assert response.actions[0].data == {"label": "default"}


@pytest.mark.asyncio
async def test_create_filter_rejects_non_filter_action() -> None:
    chat_iid = PydanticObjectId()
    user = MagicMock()
    chat = MagicMock(iid=chat_iid)
    payload = FilterCreate(
        handler="spam",
        actions=[FilterActionPayload(name="not_filter", data={})],
    )
    handler = NotFilterAction()
    services = _services(
        {"not_filter": handler.definition},
        {"not_filter": handler},
    )

    with (
        patch(
            "sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid",
            new=AsyncMock(return_value=chat),
        ),
        patch(
            "sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await create_filter(chat_iid, payload, user, services)

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["updated", "re:.*", "re:^never-matches-this-random-text$"])
async def test_update_filter_updates_modern_filter(handler: str) -> None:
    chat_iid = PydanticObjectId()
    filter_id = PydanticObjectId()
    user = MagicMock(tid=88)
    chat = MagicMock(iid=chat_iid, tid=-100456)
    filter_item = MagicMock(
        id=filter_id,
        handler="old",
        version=2,
        action=None,
        actions={"dummy": {"label": "old"}},
        time=None,
        effective_version=2,
        save=AsyncMock(),
    )
    filter_item.chat.id = chat_iid
    payload = FilterUpdate(
        handler=handler,
        actions=[FilterActionPayload(name="dummy", data={"label": "done"})],
    )

    with (
        patch(
            "sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid",
            new=AsyncMock(return_value=chat),
        ),
        patch(
            "sophie_bot.modules.filters.api.filters.FiltersModel.get_by_id",
            new=AsyncMock(return_value=filter_item),
        ),
        patch(
            "sophie_bot.modules.filters.api.utils.FiltersModel.get_by_id",
            new=AsyncMock(return_value=filter_item),
        ),
        patch(
            "sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "sophie_bot.modules.filters.api.filters.log_event",
            new=AsyncMock(),
        ),
    ):
        response = await update_filter(
            chat_iid,
            filter_id,
            payload,
            user,
            _services(),
        )

    assert filter_item.handler == handler
    assert filter_item.version == 2
    assert filter_item.action is None
    assert filter_item.actions == {"dummy": {"label": "done"}}
    assert response.actions[0].description == "Run done"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("handler", ["re:[", "re:(", "  re:*  "])
async def test_filter_api_rejects_malformed_regex(operation: str, handler: str) -> None:
    chat_iid = PydanticObjectId()
    filter_id = PydanticObjectId()
    chat = MagicMock(iid=chat_iid)
    filter_item = MagicMock(id=filter_id, handler="original", save=AsyncMock())
    filter_item.chat.id = chat_iid

    with (
        patch(
            "sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid",
            new=AsyncMock(return_value=chat),
        ),
        patch(
            "sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "sophie_bot.modules.filters.api.filters.FiltersModel.get_by_id",
            new=AsyncMock(return_value=filter_item),
        ),
        patch("sophie_bot.modules.filters.api.filters.FiltersModel.insert", new=AsyncMock()) as insert,
        pytest.raises(HTTPException) as exc_info,
    ):
        if operation == "create":
            await create_filter(
                chat_iid,
                FilterCreate(handler=handler, actions=[FilterActionPayload(name="dummy", data={})]),
                MagicMock(),
                _services(),
            )
        else:
            await update_filter(chat_iid, filter_id, FilterUpdate(handler=handler), MagicMock(), _services())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Provided regex pattern is invalid"
    insert.assert_not_awaited()
    filter_item.save.assert_not_awaited()
    assert filter_item.handler == "original"


@pytest.mark.asyncio
async def test_filter_api_regex_timeout_remains_bad_request() -> None:
    with (
        patch(
            "sophie_bot.modules.filters.api.utils.FiltersModel.get_by_keyword",
            new=AsyncMock(return_value=None),
        ),
        patch("sophie_bot.modules.filters.api.utils.regex.match", side_effect=TimeoutError) as match,
        pytest.raises(HTTPException) as exc_info,
    ):
        await validate_filter_handler(PydanticObjectId(), "re:.*")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Provided regex pattern is too slow to execute"
    match.assert_called_once()
    assert match.call_args.args[0] == ".*"
    assert match.call_args.kwargs == {"timeout": 0.2}


def test_build_filter_response_returns_modern_actions() -> None:
    filter_item = MagicMock(
        id=PydanticObjectId(),
        handler="spam",
        action=None,
        actions={"dummy": {"label": "default"}},
        time=None,
        effective_version=2,
    )

    response = build_filter_response(
        filter_item,
        DUMMY_ACTIONS,
        DUMMY_HANDLERS,
    )

    assert response.handler == "spam"
    assert response.actions[0].name == "dummy"
    assert response.actions[0].title == "Dummy"


@pytest.mark.asyncio
async def test_delete_filter_removes_existing_filter() -> None:
    chat_iid = PydanticObjectId()
    filter_id = PydanticObjectId()
    user = MagicMock(tid=77)
    chat = MagicMock(iid=chat_iid, tid=-100789)
    filter_item = MagicMock(handler="spam", delete=AsyncMock())
    filter_item.chat.id = chat_iid

    with (
        patch(
            "sophie_bot.modules.filters.api.utils.ChatModel.get_by_iid",
            new=AsyncMock(return_value=chat),
        ),
        patch(
            "sophie_bot.modules.filters.api.filters.FiltersModel.get_by_id",
            new=AsyncMock(return_value=filter_item),
        ),
        patch(
            "sophie_bot.modules.filters.api.filters.log_event",
            new=AsyncMock(),
        ),
    ):
        response = await delete_filter(chat_iid, filter_id, user)

    assert response.status_code == 204
    filter_item.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_filter_actions_returns_catalog() -> None:
    response = await list_filter_actions(
        _services(),
        SimpleNamespace(tid=1),
    )

    assert response.limits.max_ai_filters_per_chat >= 1
    assert response.actions[0].name == "dummy"
    assert response.actions[0].default_data == {"label": "default"}
