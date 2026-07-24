"""Tests for federation CSV import ban-permission checks.

Beanie's `Link.fetch()` returns the `Link` itself when the referenced document is gone, and
a `Link` is truthy - so a plain falsy check lets it through and the following attribute
access raises `AttributeError`. That escapes the per-row `BanValidationError` handler and
fails the entire import instead of skipping one row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.schedules.process_imports import (
    BanValidationError,
    ProcessFederationImports,
)


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
        await ProcessFederationImports._check_ban_permissions(owner.tid, federation, 600003)

    with pytest.raises(BanValidationError, match="admin"):
        await ProcessFederationImports._check_ban_permissions(admin.tid, federation, 600003)


@pytest.mark.asyncio
async def test_check_ban_permissions_allows_regular_user(db_init: object) -> None:
    owner = await _create_chat(600004, "Owner")
    federation = await _create_federation(owner)

    await ProcessFederationImports._check_ban_permissions(600005, federation, 600006)


@pytest.mark.asyncio
async def test_check_ban_permissions_tolerates_deleted_creator(db_init: object) -> None:
    owner = await _create_chat(600007, "Owner")
    federation = await _create_federation(owner)
    await owner.delete()

    await ProcessFederationImports._check_ban_permissions(600008, federation, 600009)


@pytest.mark.asyncio
async def test_check_ban_permissions_tolerates_deleted_admin(db_init: object) -> None:
    owner = await _create_chat(600010, "Owner")
    admin = await _create_chat(600011, "Admin")
    federation = await _create_federation(owner, admins=[admin])
    await admin.delete()

    await ProcessFederationImports._check_ban_permissions(600012, federation, 600013)
