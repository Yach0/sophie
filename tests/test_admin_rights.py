from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatMemberStatus

from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatType
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.middlewares.connections import ChatConnection


@dataclass
class FakeUserLink:
    user_model: Any

    async def fetch(self) -> Any:
        return self.user_model


@dataclass
class FakeAdminEntry:
    member: Any
    user: FakeUserLink


class FakeAdminsQuery:
    def __init__(self, admin_entries: list[FakeAdminEntry]) -> None:
        self.admin_entries = admin_entries

    async def to_list(self) -> list[FakeAdminEntry]:
        return self.admin_entries


def build_anonymous_message() -> SimpleNamespace:
    reply_method = AsyncMock()
    return SimpleNamespace(
        from_user=SimpleNamespace(id=TELEGRAM_ANONYMOUS_ADMIN_BOT_ID, first_name="GroupAnonymousBot"),
        sender_chat=SimpleNamespace(id=-100987654321),
        author_signature="Moderator",
        chat=SimpleNamespace(id=-100987654321, type="supergroup"),
        reply=reply_method,
        answer=AsyncMock(),
    )


def build_connection(chat_model: Any) -> ChatConnection:
    return ChatConnection(
        type=chat_model.type,
        is_connected=False,
        tid=chat_model.tid,
        title=chat_model.first_name_or_title,
        db_model=chat_model,
    )


@pytest.mark.asyncio
async def test_anonymous_admin_duplicate_title_all_have_permissions(
    monkeypatch: pytest.MonkeyPatch,
    db_init: Any,
) -> None:
    admin_filter = UserRestricting(can_restrict_members=True)

    message = build_anonymous_message()
    chat_model = SimpleNamespace(
        iid="chat_iid",
        tid=-100987654321,
        type=ChatType.supergroup,
        first_name_or_title="Forum Chat",
    )
    connection = build_connection(chat_model)

    matched_admins = [
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=True,
            ),
            user=FakeUserLink(user_model=SimpleNamespace(iid="resolved_admin_iid", tid=111111)),
        ),
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=True,
            ),
            user=FakeUserLink(user_model=None),
        ),
    ]

    from sophie_bot.db.models.chat_admin import ChatAdminModel

    monkeypatch.setattr(ChatAdminModel, "find", lambda *args, **kwargs: FakeAdminsQuery(matched_admins))

    result = await admin_filter(message, connection=connection, user_db=None)

    assert isinstance(result, dict)
    assert result["user_db"] == matched_admins[0].user.user_model
    assert message.reply.await_count == 0


@pytest.mark.asyncio
async def test_anonymous_admin_duplicate_title_mixed_permissions_denied(
    monkeypatch: pytest.MonkeyPatch,
    db_init: Any,
) -> None:
    admin_filter = UserRestricting(can_restrict_members=True)

    message = build_anonymous_message()
    chat_model = SimpleNamespace(
        iid="chat_iid",
        tid=-100987654321,
        type=ChatType.supergroup,
        first_name_or_title="Forum Chat",
    )
    connection = build_connection(chat_model)

    matched_admins = [
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=True,
            ),
            user=FakeUserLink(user_model=None),
        ),
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=False,
            ),
            user=FakeUserLink(user_model=None),
        ),
    ]

    from sophie_bot.db.models.chat_admin import ChatAdminModel

    monkeypatch.setattr(ChatAdminModel, "find", lambda *args, **kwargs: FakeAdminsQuery(matched_admins))

    with pytest.raises(SkipHandler):
        await admin_filter(message, connection=connection, user_db=None)

    assert message.reply.await_count >= 1
    first_reply_call = message.reply.await_args_list[0]
    assert "Multiple anonymous admins share this title" in first_reply_call.args[0]
