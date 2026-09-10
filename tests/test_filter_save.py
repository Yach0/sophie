from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.modules.filters.filter_wizard import FilterDraft, _save_filter
from sophie_bot.modules.filters.utils_.filter_handler_rules import InvalidFilterHandler


@pytest.mark.asyncio
async def test_filter_draft_round_trip_preserves_handler_and_silent_mode() -> None:
    draft = FilterDraft(handler="spam", actions={"kick_user": None}, silent=True)
    restored = FilterDraft.model_validate(draft.model_dump(mode="json"))
    assert restored == draft


@pytest.mark.asyncio
async def test_filter_save_revalidates_before_persisting() -> None:
    chat_iid = PydanticObjectId()
    draft = FilterDraft(handler="spam", actions={"kick_user": None})
    callback = SimpleNamespace(from_user=None)
    connection = SimpleNamespace(tid=-100)
    with (
        patch(
            "sophie_bot.modules.filters.filter_wizard.validate_filter_handler",
            AsyncMock(side_effect=InvalidFilterHandler("duplicate")),
        ) as validate,
        pytest.raises(InvalidFilterHandler, match="duplicate"),
    ):
        await _save_filter(chat_iid, draft, callback, connection)

    validate.assert_awaited_once_with(chat_iid, "spam", None)

@pytest.mark.asyncio
async def test_invalid_filter_id_is_rejected_without_loading_model(db_init: Any) -> None:
    del db_init
    draft = FilterDraft(filter_id="invalid", handler="spam", actions={"kick_user": None})
    with (
        patch("sophie_bot.modules.filters.filter_wizard.validate_filter_handler", AsyncMock()),
        patch.object(FiltersModel, "find_one", AsyncMock()) as find_one_mock,
        pytest.raises(ValueError, match="could not be found"),
    ):
        await _save_filter(PydanticObjectId(), draft, SimpleNamespace(from_user=None), SimpleNamespace(tid=-100))
    find_one_mock.assert_not_called()

    non_existent_id = str(PydanticObjectId())
    draft_non_existent = FilterDraft(filter_id=non_existent_id, handler="spam", actions={"kick_user": None})
    with (
        patch("sophie_bot.modules.filters.filter_wizard.validate_filter_handler", AsyncMock()),
        patch.object(FiltersModel, "find_one", AsyncMock(return_value=None)) as find_one_mock,
        pytest.raises(ValueError, match="could not be found"),
    ):
        await _save_filter(PydanticObjectId(), draft_non_existent, SimpleNamespace(from_user=None), SimpleNamespace(tid=-100))
    find_one_mock.assert_awaited_once()
