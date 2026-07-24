from __future__ import annotations

import time
from typing import Any

import pytest

from sophie_bot import healthcheck
from sophie_bot.services import health
from sophie_bot.services.health import check_heartbeat, write_heartbeat


async def test_write_then_check_is_fresh() -> None:
    await write_heartbeat("bot")

    assert await check_heartbeat("bot", max_age_seconds=health.HEARTBEAT_TTL_SECONDS) is True


async def test_check_missing_component_is_unhealthy() -> None:
    assert await check_heartbeat("scheduler", max_age_seconds=health.HEARTBEAT_TTL_SECONDS) is False


async def test_check_stale_heartbeat_is_unhealthy() -> None:
    # Write a timestamp well outside the freshness window (TTL is longer so the key survives).
    stale_ts = int(time.time()) - (health.HEARTBEAT_TTL_SECONDS + 60)
    await health.aredis.set("sophie:health:bot", stale_ts, ex=health.HEARTBEAT_TTL_SECONDS)

    assert await check_heartbeat("bot", max_age_seconds=health.HEARTBEAT_TTL_SECONDS) is False


async def test_write_heartbeat_sets_ttl() -> None:
    await write_heartbeat("bot")

    ttl = await health.aredis.ttl("sophie:health:bot")
    assert 0 < ttl <= health.HEARTBEAT_TTL_SECONDS


async def test_cli_bot_mode_uses_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(healthcheck.CONFIG, "mode", "bot")
    await write_heartbeat("bot")

    healthy, status = await healthcheck._run()

    assert healthy is True
    assert "bot" in status


async def test_cli_scheduler_mode_unhealthy_without_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(healthcheck.CONFIG, "mode", "scheduler")

    healthy, status = await healthcheck._run()

    assert healthy is False
    assert "scheduler" in status


async def test_cli_rest_mode_dispatches_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(healthcheck.CONFIG, "mode", "rest")

    called: dict[str, bool] = {}

    async def fake_check_rest() -> tuple[bool, str]:
        called["rest"] = True
        return True, "rest: ok"

    monkeypatch.setattr(healthcheck, "_check_rest", fake_check_rest)

    healthy, status = await healthcheck._run()

    assert called.get("rest") is True
    assert healthy is True
    assert status == "rest: ok"


async def test_cli_unknown_mode_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(healthcheck.CONFIG, "mode", "nostart")

    healthy, _status = await healthcheck._run()

    assert healthy is False


async def test_cli_rest_check_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"status": "ok"}

    class FakeClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(healthcheck.httpx, "AsyncClient", FakeClient)

    healthy, status = await healthcheck._check_rest()

    assert healthy is True
    assert status == "rest: ok"


def test_rest_probe_host_maps_wildcard_bind_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(healthcheck.CONFIG, "api_listen", "0.0.0.0")
    assert healthcheck._rest_probe_host() == "127.0.0.1"


def test_rest_probe_host_keeps_explicit_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(healthcheck.CONFIG, "api_listen", "10.0.0.5")
    assert healthcheck._rest_probe_host() == "10.0.0.5"
