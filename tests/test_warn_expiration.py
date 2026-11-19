from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram_test_framework.factories import ChatFactory, UserFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.modules.warns.api.get_user_warns import get_user_warns
from sophie_bot.modules.warns.handlers.warns_pm import WarnsPMHandler
from sophie_bot.modules.warns.utils import expire_warns
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import get_default_value, set_chat_override, set_enabled
from tests.utils.db_fixture import cleanup_beanie

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _clean_database(db_init: object) -> AsyncGenerator[None]:
    await cleanup_beanie()
    yield
    await cleanup_beanie()


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return NOW


class LaterDateTime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return NOW + timedelta(hours=1)


async def _models() -> tuple[ChatModel, ChatModel]:
    chat = await ChatModel.upsert_group(ChatFactory.create_group(chat_id=-100912345, title="Expiration group"))
    user = await ChatModel.upsert_user(UserFactory.create(user_id=912345, first_name="Target"))
    return chat, user


async def _configure(chat: ChatModel, services: ApplicationServices) -> None:
    settings = await WarnSettingsModel.get_or_create(chat.iid)
    settings.expiration_seconds = 3600
    await settings.save()
    await set_chat_override("warn_expiration", chat.tid, True, redis=services.redis)


@pytest.mark.asyncio
async def test_default_disabled(test_services: ApplicationServices) -> None:
    chat, _user = await _models()
    assert (await WarnSettingsModel.get_or_create(chat.iid)).expiration_seconds is None
    assert get_default_value("warn_expiration") is False


@pytest.mark.asyncio
async def test_cutoff_is_inclusive_and_scoped_to_chat(
    test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat, user = await _models()
    await _configure(chat, test_services)
    monkeypatch.setattr("sophie_bot.modules.warns.utils.datetime", FrozenDateTime)
    boundary = NOW - timedelta(hours=1)
    records = [
        WarnModel(chat=chat, user=user, date=boundary - timedelta(milliseconds=1)),
        WarnModel(chat=chat, user=user, date=boundary.replace(tzinfo=None)),
        WarnModel(chat=chat, user=user, date=boundary + timedelta(milliseconds=1)),
    ]
    for record in records:
        await record.insert()
    other_chat = await ChatModel.upsert_group(ChatFactory.create_group(chat_id=-100912346, title="Other group"))
    other = WarnModel(chat=other_chat, user=user, date=boundary)
    await other.insert()

    await expire_warns(chat, redis=test_services.redis)

    assert await WarnModel.get(records[0].id) is None
    assert await WarnModel.get(records[1].id) is None
    assert await WarnModel.get(records[2].id) is not None
    assert await WarnModel.get(other.id) is not None
    # Advancing only the clock expires the surviving individual warning.
    monkeypatch.setattr("sophie_bot.modules.warns.utils.datetime", LaterDateTime)
    await expire_warns(chat, redis=test_services.redis)
    assert await WarnModel.get(records[2].id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["missing", "null", "iso_string", "empty_object", "numeric", "date_array"])
async def test_legacy_unknown_dates_are_retained_without_refreshing(
    test_services: ApplicationServices, shape: str
) -> None:
    chat, user = await _models()
    await _configure(chat, test_services)
    warning = WarnModel(chat=chat, user=user, reason="Legacy")
    await warning.insert()
    collection = WarnModel.get_pymongo_collection()
    if shape == "missing":
        await collection.update_one({"_id": warning.id}, {"$unset": {"date": ""}, "$set": {"admin": {}}})
    else:
        value = {
            "null": None,
            "iso_string": "2020-01-01T00:00:00Z",
            "empty_object": {},
            "numeric": 123,
            "date_array": [datetime(2020, 1, 1)],
        }[shape]
        await collection.update_one({"_id": warning.id}, {"$set": {"date": value, "admin": {}}})

    await expire_warns(chat, redis=test_services.redis)
    first_read = await WarnModel.get(warning.id)
    second_read = await WarnModel.get(warning.id)
    assert first_read is not None and second_read is not None
    assert first_read.date == second_read.date
    assert first_read.date is None
    assert first_read.admin is None
    assert await WarnModel.count_user_warns(chat.iid, user.iid) == 1
    raw = await collection.find_one({"_id": warning.id})
    assert raw is not None
    if shape == "missing":
        assert "date" not in raw
    else:
        assert raw["date"] == value


@pytest.mark.asyncio
async def test_new_warnings_have_creation_date(test_services: ApplicationServices) -> None:
    chat, user = await _models()
    before = datetime.now(UTC)
    warning = WarnModel(chat=chat, user=user)
    assert warning.date is not None and warning.date >= before


@pytest.mark.asyncio
async def test_chat_flag_override_controls_runtime(test_services: ApplicationServices) -> None:
    chat, user = await _models()
    await _configure(chat, test_services)
    await set_enabled("warn_expiration", True, redis=test_services.redis)
    await set_chat_override("warn_expiration", chat.tid, False, redis=test_services.redis)
    warning = WarnModel(chat=chat, user=user, date=NOW - timedelta(days=365))
    await warning.insert()
    await expire_warns(chat, redis=test_services.redis)
    assert await WarnModel.get(warning.id) is not None
    await set_chat_override("warn_expiration", chat.tid, True, redis=test_services.redis)
    await expire_warns(chat, redis=test_services.redis)
    assert await WarnModel.get(warning.id) is None


@pytest.mark.asyncio
async def test_api_listing_excludes_expired_and_reports_unknown_date(test_services: ApplicationServices) -> None:
    chat, user = await _models()
    await _configure(chat, test_services)
    expired = WarnModel(chat=chat, user=user, reason="Expired", date=NOW - timedelta(days=365))
    legacy = WarnModel(chat=chat, user=user, reason="Unknown date", date=None)
    fresh = WarnModel(chat=chat, user=user, reason="Fresh")
    for warning in (expired, legacy, fresh):
        await warning.insert()

    response = await get_user_warns(chat, user.tid, user, test_services)

    assert {warning.reason for warning in response} == {"Unknown date", "Fresh"}
    assert next(warning for warning in response if warning.reason == "Unknown date").date is None
    assert next(warning for warning in response if warning.reason == "Fresh").date is not None


@pytest.mark.asyncio
async def test_pm_listing_reports_empty_after_expiration(test_services: ApplicationServices) -> None:
    chat, user = await _models()
    await _configure(chat, test_services)
    await WarnModel(chat=chat, user=user, reason="Expired", date=NOW - timedelta(days=365)).insert()
    message = SimpleNamespace(reply=AsyncMock())
    await WarnsPMHandler(message, context=SimpleNamespace(actor=user), services=test_services).handle()
    assert "don't have warnings" in message.reply.await_args.args[0]
