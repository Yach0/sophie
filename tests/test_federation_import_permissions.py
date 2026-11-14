"""Tests for federation CSV import permissions and duplicate rows.

Beanie's `Link.fetch()` returns the `Link` itself when the referenced document is gone, and
a `Link` is truthy - so a plain falsy check lets it through and the following attribute
access raises `AttributeError`. That escapes the per-row `BanValidationError` handler and
fails the entire import instead of skipping one row.
"""

from __future__ import annotations

import csv
import uuid
from datetime import UTC, datetime
from io import BytesIO, StringIO
from unittest.mock import AsyncMock

import pytest
from aiogram.types import File

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation, FederationBan, FederationTask
from sophie_bot.db.models.federations_enums import FederationTaskType
from sophie_bot.modules.federations.schedules import process_imports
from sophie_bot.modules.federations.schedules.process_imports import (
    BanValidationError,
    _execute_import_task,
    check_ban_permissions,
)
from sophie_bot.modules.federations.utils.cache_service import FederationCacheService
from sophie_bot.services.application import ApplicationServices


async def _create_chat(chat_tid: int, title: str) -> ChatModel:
    chat = ChatModel(
        tid=chat_tid,
        type=ChatType.group,
        first_name_or_title=title,
        last_name=None,
        username=None,
        language_code=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await chat.save()
    return chat


async def _create_federation(creator: ChatModel, admins: list[ChatModel] | None = None) -> Federation:
    federation = Federation(
        fed_name=f"fed-{uuid.uuid4()}",
        fed_id=str(uuid.uuid4()),
        creator=creator,
        admins=admins or [],
    )
    await federation.insert()
    stored = await Federation.get(federation.id)
    assert stored is not None
    return stored


@pytest.mark.asyncio
async def test_check_ban_permissions_rejects_owner_and_admin(db_init: object) -> None:
    owner = await _create_chat(600001, "Owner")
    admin = await _create_chat(600002, "Admin")
    federation = await _create_federation(owner, admins=[admin])

    with pytest.raises(BanValidationError, match="owner"):
        await check_ban_permissions(owner.tid, federation, 600003)

    with pytest.raises(BanValidationError, match="admin"):
        await check_ban_permissions(admin.tid, federation, 600003)


@pytest.mark.asyncio
async def test_check_ban_permissions_allows_regular_user(db_init: object) -> None:
    owner = await _create_chat(600004, "Owner")
    federation = await _create_federation(owner)

    await check_ban_permissions(600005, federation, 600006)


@pytest.mark.asyncio
async def test_check_ban_permissions_tolerates_deleted_creator(db_init: object) -> None:
    owner = await _create_chat(600007, "Owner")
    federation = await _create_federation(owner)
    await owner.delete()

    await check_ban_permissions(600008, federation, 600009)


@pytest.mark.asyncio
async def test_check_ban_permissions_tolerates_deleted_admin(db_init: object) -> None:
    owner = await _create_chat(600010, "Owner")
    admin = await _create_chat(600011, "Admin")
    federation = await _create_federation(owner, admins=[admin])
    await admin.delete()

    await check_ban_permissions(600012, federation, 600013)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [100, 2])
@pytest.mark.parametrize("already_banned", [False, True])
@pytest.mark.parametrize("final_reason", ["updated reason", ""])
async def test_import_duplicate_rows_preserves_unique_bans_and_counts(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    batch_size: int,
    already_banned: bool,
    final_reason: str,
) -> None:
    owner = await _create_chat(610001, "Owner")
    importer = await _create_chat(610002, "Importer")
    federation = await _create_federation(owner)
    task = FederationTask(
        fed_id=federation.fed_id,
        task_type=FederationTaskType.IMPORT,
        chat=importer,
        user=importer,
        created_at=datetime.now(UTC),
        file_id="banlist.csv",
    )
    await task.insert()
    stored_task = await FederationTask.get(task.id)
    assert stored_task is not None
    initial_time = datetime(2024, 1, 1, tzinfo=UTC)
    if already_banned:
        await FederationBan(
            fed_id=federation.fed_id,
            user_id=610003,
            by=owner,
            time=initial_time,
            reason="stored reason",
        ).insert()

    csv_file = StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(["user_id", "reason", "by", "time"])
    writer.writerows(
        [
            [610003, "first reason", owner.tid, initial_time.isoformat()],
            [610003, "first reason", owner.tid, initial_time.isoformat()],
            [610004, "another user", owner.tid, initial_time.isoformat()],
            [610003, final_reason, importer.tid, "2025-01-01T00:00:00+00:00"],
            [610003, "invalid author", 619999, initial_time.isoformat()],
            [610003, "invalid time", owner.tid, "invalid"],
            [610003, "x" * (process_imports.MAX_REASON_LENGTH + 1), owner.tid, initial_time.isoformat()],
            [owner.tid, "protected user", importer.tid, initial_time.isoformat()],
            [owner.tid, "protected duplicate", importer.tid, initial_time.isoformat()],
        ]
    )
    monkeypatch.setattr(process_imports, "BATCH_SIZE", batch_size)
    monkeypatch.setattr(
        test_services.bot,
        "get_file",
        AsyncMock(return_value=File(file_id=task.file_id, file_unique_id="banlist", file_path="banlist.csv")),
    )
    monkeypatch.setattr(
        test_services.bot, "download_file", AsyncMock(return_value=BytesIO(csv_file.getvalue().encode()))
    )
    await FederationCacheService.set_ban_count(federation.fed_id, int(already_banned), redis=test_services.redis)

    result = await _execute_import_task(test_services, stored_task)

    assert result["imported_count"] == 4
    assert result["failed_count"] == 5
    bans = await FederationBan.find(FederationBan.fed_id == federation.fed_id).to_list()
    assert len(bans) == 2
    bans_by_user = {ban.user_id: ban for ban in bans}
    assert set(bans_by_user) == {610003, 610004}
    repeated_ban = bans_by_user[610003]
    assert repeated_ban.reason == (final_reason or None)
    assert repeated_ban.time.replace(tzinfo=UTC) == initial_time
    assert (await repeated_ban.by.fetch()).iid == owner.iid
    assert repeated_ban.fimport_id == (None if already_banned else task.id)
    assert bans_by_user[610004].reason == "another user"
    assert await FederationCacheService.get_ban_count(federation.fed_id, redis=test_services.redis) == 2
