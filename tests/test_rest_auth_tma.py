from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qsl, urlencode

import pytest
from fastapi import HTTPException
from init_data_py import sign

from sophie_bot.modules.rest.api import auth

BOT_TOKEN = "12345:dummy-test-token"
USER_ID = 123456


def signed_data(age: timedelta = timedelta(), include_user: bool = True) -> str:
    payload = {"user": json.dumps({"id": USER_ID, "first_name": "Test"})} if include_user else {}
    return sign(payload, BOT_TOKEN, auth_date=datetime.now(UTC) - age)


@pytest.fixture
def auth_mocks(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    lookup = AsyncMock(return_value=MagicMock(tid=USER_ID))
    tokens = AsyncMock(return_value={"access_token": "access", "refresh_token": "refresh", "token_type": "bearer"})
    monkeypatch.setattr(auth.CONFIG, "token", BOT_TOKEN)
    monkeypatch.setattr(auth.ChatModel, "get_by_tid", lookup)
    monkeypatch.setattr(auth, "create_tokens", tokens)
    return lookup, tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [timedelta(), timedelta(hours=23, minutes=59)])
async def test_login_tma_accepts_valid_data(age: timedelta, auth_mocks: tuple[AsyncMock, AsyncMock]) -> None:
    lookup, tokens = auth_mocks
    result = await auth.login_tma(auth.TMALoginRequest(initData=signed_data(age)))
    lookup.assert_awaited_once_with(USER_ID)
    tokens.assert_awaited_once_with(lookup.return_value)
    assert result == tokens.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "status"),
    [
        ("malformed", 400),
        ("missing_hash", 400),
        ("missing_auth_date", 400),
        ("duplicate", 400),
        ("tampered", 401),
        ("expired", 401),
        ("missing_user", 400),
    ],
)
async def test_login_tma_rejects_invalid_data(case: str, status: int, auth_mocks: tuple[AsyncMock, AsyncMock]) -> None:
    lookup, tokens = auth_mocks
    raw = signed_data(timedelta(days=1, seconds=1) if case == "expired" else timedelta(), case != "missing_user")
    fields = dict(parse_qsl(raw))
    if case == "malformed":
        raw = "not-a-query-string"
    elif case in ("missing_hash", "missing_auth_date"):
        del fields[case.removeprefix("missing_")]
        raw = urlencode(fields)
    elif case == "duplicate":
        raw += "&auth_date=" + fields["auth_date"]
    elif case == "tampered":
        fields["user"] = json.dumps({"id": USER_ID + 1, "first_name": "Forged"})
        raw = urlencode(fields)

    with pytest.raises(HTTPException) as exc_info:
        await auth.login_tma(auth.TMALoginRequest(initData=raw))

    assert exc_info.value.status_code == status
    lookup.assert_not_awaited()
    tokens.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_tma_rejects_unknown_user(auth_mocks: tuple[AsyncMock, AsyncMock]) -> None:
    lookup, tokens = auth_mocks
    lookup.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        await auth.login_tma(auth.TMALoginRequest(initData=signed_data()))
    assert exc_info.value.status_code == 403
    lookup.assert_awaited_once_with(USER_ID)
    tokens.assert_not_awaited()
