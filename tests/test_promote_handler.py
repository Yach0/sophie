from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import User

from sophie_bot.config import CONFIG
from sophie_bot.modules.promotes.handlers.promote import PROMOTE_PERMISSIONS, PromoteUserHandler


@pytest.mark.asyncio
async def test_promote_operator_grants_all_permissions_without_chat_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = User(id=123456, is_bot=False, first_name="Operator")
    target = User(id=654321, is_bot=False, first_name="Target")
    promote_chat_member = AsyncMock()
    services = SimpleNamespace(bot=SimpleNamespace(promote_chat_member=promote_chat_member))
    event = SimpleNamespace(from_user=invoker, reply_to_message=None, reply=AsyncMock())
    connection = SimpleNamespace(tid=-100123, title="Test group", db_model=SimpleNamespace())
    context = SimpleNamespace(connection=connection)
    handler = PromoteUserHandler(
        event,
        user=target,
        admin_title=None,
        context=context,
        services=services,
    )

    monkeypatch.setattr(CONFIG, "operators", [invoker.id])
    monkeypatch.setattr(
        "sophie_bot.modules.promotes.handlers.promote.get_admins_rights",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.promotes.handlers.promote.reply_or_answer",
        AsyncMock(),
    )

    await handler.handle()

    promote_chat_member.assert_awaited_once_with(
        chat_id=connection.tid,
        user_id=target.id,
        **{permission: True for permission in PROMOTE_PERMISSIONS},
    )
