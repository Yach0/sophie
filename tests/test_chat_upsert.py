"""Chat upserts must return the stored identity and preserve it across updates."""

from __future__ import annotations

from typing import Any

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


async def test_upsert_user_returns_stored_id_on_insert(db_init: Any) -> None:
    """The insert path is the broken one: Beanie returns the on_insert template, not a read."""
    user_tid = 880001

    inserted = await ChatModel.upsert_user(_user(user_tid))

    assert inserted.id == await _stored_id(user_tid)


async def test_upsert_user_returns_stored_id_on_update(db_init: Any) -> None:
    user_tid = 880002
    await ChatModel.upsert_user(_user(user_tid))

    updated = await ChatModel.upsert_user(_user(user_tid))

    assert updated.id == await _stored_id(user_tid)


async def test_upsert_group_returns_stored_id_on_insert(db_init: Any) -> None:
    chat_tid = -880003

    inserted = await ChatModel.upsert_group(_group(chat_tid))

    assert inserted.id == await _stored_id(chat_tid)


async def test_upserted_user_is_findable_by_its_own_id(db_init: Any) -> None:
    user_tid = 880004

    inserted = await ChatModel.upsert_user(_user(user_tid))

    assert await ChatModel.get_by_iid(inserted.id) is not None


async def test_saving_a_freshly_upserted_user_does_not_alter_id(db_init: Any) -> None:
    user_tid = 880005
    inserted = await ChatModel.upsert_user(_user(user_tid))

    inserted.first_name_or_title = "Renamed"
    await inserted.save()

    reloaded = await ChatModel.get_by_tid(user_tid)
    assert reloaded
    assert reloaded.first_name_or_title == "Renamed"


async def test_upsert_user_id_is_stable_across_calls(db_init: Any) -> None:
    user_tid = 880006
    first = await ChatModel.upsert_user(_user(user_tid))
    second = await ChatModel.upsert_user(_user(user_tid))

    assert first.id == second.id
