"""The mock must resolve Beanie Link queries the way real MongoDB does.

Beanie compiles `Model.chat.id == chat_iid` to `{"chat.$id": ...}`. mongomock cannot traverse
a DBRef on its own, so tests/utils/mongo_mock.py patches it. If that patch ever stops working,
every Link lookup in the suite silently starts matching nothing and the tests that rely on one
go quietly vacuous rather than failing -- so pin it here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sophie_bot.db.models.chat import ChatModel, ChatType, UserInGroupModel


async def _chat(chat_tid: int, chat_type: ChatType) -> ChatModel:
    await ChatModel(
        tid=chat_tid,
        type=chat_type,
        first_name_or_title="Link probe",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()

    chat = await ChatModel.get_by_tid(chat_tid)
    assert chat
    return chat


async def test_link_query_matches_the_referenced_document(db_init: Any) -> None:
    user = await _chat(990001, ChatType.private)
    group = await _chat(-990002, ChatType.supergroup)
    await UserInGroupModel.ensure_user_in_group(user, group)

    found = await UserInGroupModel.get_user_in_group(user.iid, group.iid)

    assert found is not None


async def test_link_query_does_not_match_a_different_document(db_init: Any) -> None:
    """A Link lookup that matches everything would be as broken as one that matches nothing."""
    user = await _chat(990003, ChatType.private)
    group = await _chat(-990004, ChatType.supergroup)
    other_group = await _chat(-990005, ChatType.supergroup)
    await UserInGroupModel.ensure_user_in_group(user, group)

    assert await UserInGroupModel.get_user_in_group(user.iid, other_group.iid) is None
