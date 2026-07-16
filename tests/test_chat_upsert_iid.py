"""ChatModel.upsert_* must return a document whose iid matches the stored _id.

ChatModel declares both Beanie's `Document.id` and its own `iid`, each aliased to `_id`.
They are separate fields, so they can disagree -- and on the upsert insert path they did.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.types import Chat, User

from sophie_bot.db.models.chat import ChatModel


def _user(user_tid: int) -> User:
    return User(id=user_tid, is_bot=False, first_name="Upsert", username=f"upsert{user_tid}")


def _group(chat_tid: int) -> Chat:
    return Chat(id=chat_tid, type="supergroup", title="Upsert group")


async def _stored_id(chat_tid: int) -> Any:
    raw = await ChatModel.get_pymongo_collection().find_one({"chat_id": chat_tid})
    assert raw is not None
    return raw["_id"]


async def test_upsert_user_returns_stored_iid_on_insert(db_init: Any) -> None:
    """The insert path is the broken one: Beanie returns the on_insert template, not a read."""
    user_tid = 880001

    inserted = await ChatModel.upsert_user(_user(user_tid))

    assert inserted.iid == inserted.id
    assert inserted.iid == await _stored_id(user_tid)


async def test_upsert_user_returns_stored_iid_on_update(db_init: Any) -> None:
    user_tid = 880002
    await ChatModel.upsert_user(_user(user_tid))

    updated = await ChatModel.upsert_user(_user(user_tid))

    assert updated.iid == updated.id
    assert updated.iid == await _stored_id(user_tid)


async def test_upsert_group_returns_stored_iid_on_insert(db_init: Any) -> None:
    chat_tid = -880003

    inserted = await ChatModel.upsert_group(_group(chat_tid))

    assert inserted.iid == inserted.id
    assert inserted.iid == await _stored_id(chat_tid)


async def test_upserted_user_is_findable_by_its_own_iid(db_init: Any) -> None:
    """A phantom iid silently matches no document, which is how this reaches callers."""
    user_tid = 880004

    inserted = await ChatModel.upsert_user(_user(user_tid))

    assert await ChatModel.get_by_iid(inserted.iid) is not None


async def test_saving_a_freshly_upserted_user_does_not_alter_id(db_init: Any) -> None:
    """A disagreeing iid makes save() try to rewrite the immutable _id and raise."""
    user_tid = 880005
    inserted = await ChatModel.upsert_user(_user(user_tid))

    inserted.first_name_or_title = "Renamed"
    await inserted.save()

    reloaded = await ChatModel.get_by_tid(user_tid)
    assert reloaded
    assert reloaded.first_name_or_title == "Renamed"


@pytest.mark.parametrize("user_tid", [880006])
async def test_upsert_user_iid_is_stable_across_calls(db_init: Any, user_tid: int) -> None:
    first = await ChatModel.upsert_user(_user(user_tid))
    second = await ChatModel.upsert_user(_user(user_tid))

    assert first.iid == second.iid
