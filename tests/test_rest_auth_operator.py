from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from sophie_bot.modules.rest.api import auth
from sophie_bot.modules.rest.api.auth import OperatorLoginRequest, login_operator

UNKNOWN_TOKEN = "definitely-not-a-valid-operator-token"


class RecordingLog:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        return None

    def error(self, event: str, **kwargs: Any) -> None:
        return None


@pytest.fixture
def recorded_security_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    recorder = RecordingLog()
    monkeypatch.setattr(auth, "security_log", recorder)
    return recorder


@pytest.mark.asyncio
async def test_login_operator_rejects_unknown_token_with_401(
    db_init: Any,
    monkeypatch: pytest.MonkeyPatch,
    recorded_security_log: RecordingLog,
) -> None:
    # An unset static token forces the lookup down the ApiTokenModel path.
    monkeypatch.setattr(auth.CONFIG, "api_operator_token", None)

    with pytest.raises(HTTPException) as exc_info:
        await login_operator(OperatorLoginRequest(token=UNKNOWN_TOKEN))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


@pytest.mark.asyncio
async def test_login_operator_security_logs_failed_attempt(
    db_init: Any,
    monkeypatch: pytest.MonkeyPatch,
    recorded_security_log: RecordingLog,
) -> None:
    monkeypatch.setattr(auth.CONFIG, "api_operator_token", None)

    with pytest.raises(HTTPException):
        await login_operator(OperatorLoginRequest(token=UNKNOWN_TOKEN))

    assert [event for event, _kwargs in recorded_security_log.warnings] == ["auth.operator.login_failed"]
