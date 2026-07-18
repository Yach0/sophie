"""Tests for the federation management REST routers.

The federation link fields are stored as DBRefs, which mongomock cannot traverse
(``{"creator.$id": ...}`` matches nothing against it even though it is the shape real
MongoDB requires). Queries are therefore asserted on the rendered filter rather than on
mongomock's execution, while the response builders run against real Beanie documents so
that unfetched-``Link`` attribute access fails the test the way it fails in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.api.routers.manage import (
    create_federation,
    get_federation,
    list_federations,
)
from sophie_bot.modules.federations.api.schemas import FederationCreate


async def _create_chat(chat_tid: int, title: str) -> ChatModel:
    chat = ChatModel(
        tid=chat_tid,
        type=ChatType.group,
        first_name_or_title=title,
        last_name=None,
        username=None,
        language_code=None,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    )
    await chat.save()
    return chat


async def _create_federation(name: str, creator: ChatModel, **kwargs: Any) -> Federation:
    """Insert a federation and return it re-read from the database.

    Assigning model instances leaves them un-wrapped in memory; only a document parsed
    back out of MongoDB carries the `Link` values that production code actually receives.
    """
    federation = Federation(fed_name=name, fed_id=str(uuid.uuid4()), creator=creator, **kwargs)
    await federation.insert()
    stored = await Federation.get(federation.id)
    assert stored is not None
    return stored


@pytest.mark.asyncio
async def test_list_federations_queries_owned_and_admined(db_init: object) -> None:
    """`admins` holds a list of DBRefs, so the filter must address `admins.$id`.

    Comparing the field to a bare ObjectId renders `{"admins": ObjectId(...)}`, which can
    never match a stored `[DBRef(...)]` - a confirmed fed admin would see an empty list.
    """
    owner = await _create_chat(1002, "Owner")
    other_owner = await _create_chat(2002, "Other owner")

    owned = await _create_federation("Owned", owner)
    admined = await _create_federation("Admined", other_owner, admins=[owner])

    real_find = Federation.find
    queries: list[dict[str, Any]] = []
    results = iter([[owned], [admined]])

    def _find(expression: Any) -> Any:
        query = real_find(expression)
        queries.append(query.get_filter_query())
        result_batch = next(results)
        query.to_list = AsyncMock(return_value=result_batch)
        return query

    with patch.object(Federation, "find", _find):
        response = await list_federations(owner)

    assert queries == [{"creator.$id": owner.iid}, {"admins.$id": owner.iid}]
    assert {item.fed_id for item in response} == {owned.fed_id, admined.fed_id}
    assert {item.creator_iid for item in response} == {owner.iid, other_owner.iid}


@pytest.mark.asyncio
async def test_create_federation_returns_summary(db_init: object) -> None:
    user = await _create_chat(1005, "Creator")

    response = await create_federation(FederationCreate(name="Created"), user)

    assert response.fed_name == "Created"
    assert response.creator_iid == user.iid
    assert response.log_chat_iid is None


@pytest.mark.asyncio
async def test_get_federation_detail_resolves_links(db_init: object) -> None:
    owner = await _create_chat(1006, "Owner")
    log_chat = await _create_chat(-1001, "Log")
    member_chat = await _create_chat(-1002, "Member")

    federation = await _create_federation("Detailed", owner, chats=[member_chat], log_chat=log_chat)

    response = await get_federation(federation.fed_id, owner)

    assert response.creator_iid == owner.iid
    assert response.log_chat_iid == log_chat.iid
    assert response.chat_iids == [member_chat.iid]
