from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules.welcomesecurity.handlers.legacy_button import LegacyWSButtonHandler


@pytest.mark.asyncio
async def test_legacy_ws_button_allows_join_request_user_without_group_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = SimpleNamespace(text="/start btnwelcomesecuritystart_-100123")
    user_db = SimpleNamespace(iid="user_iid", tid=123)
    group_db = SimpleNamespace(iid="group_iid")
    ws_user = SimpleNamespace(is_join_request=True)
    greetings = SimpleNamespace(welcome_security=SimpleNamespace(enabled=True))

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.bot.get_chat_member",
        AsyncMock(return_value=SimpleNamespace(status="member")),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.FederationManageService.get_federation_for_chat",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.ChatModel.get_by_tid",
        AsyncMock(return_value=group_db),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.WSUserModel.is_user",
        AsyncMock(return_value=ws_user),
    )
    get_user_in_group = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.UserInGroupModel.get_user_in_group",
        get_user_in_group,
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=greetings),
    )
    captcha_handle = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.CaptchaGetHandler.handle",
        captcha_handle,
    )

    handler = LegacyWSButtonHandler(message, user_db=user_db, state=SimpleNamespace())

    await handler.handle()

    assert get_user_in_group.await_count == 0
    assert captcha_handle.await_count == 1
