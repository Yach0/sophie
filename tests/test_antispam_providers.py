from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from time import time

import httpx
import httpx2
import pytest
from fakeredis import FakeAsyncRedis
from pydantic import ValidationError

from sophie_bot.config import Config, OfdServerConfig
from sophie_bot.modules.antispam import cas
from sophie_bot.modules.antispam.ofd import OfdProvider
from sophie_bot.modules.antispam.provider import ProtectionUnavailable, ThreatCategory
from sophie_bot.modules.antispam.registry import ProviderRegistry

EXPORT_URL = "https://cas.example/export.csv"


class PausedExport(httpx2.AsyncByteStream):
    def __init__(self) -> None:
        self.staged = asyncio.Event()
        self.resume = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"user_id,offenses\n202,3\n"
        self.staged.set()
        await self.resume.wait()
        yield b"203,2\n"


async def test_cas_mirror_swaps_after_complete_download_and_keeps_conditional_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cas, "CSV_BATCH_SIZE", 1)
    pending = PausedExport()
    calls = 0

    def export(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == EXPORT_URL
        if calls == 1:
            return httpx2.Response(
                200, content=b"id\n101\n", headers={"ETag": '"v1"', "Last-Modified": "Wed, 25 Nov 2026 10:00:00 GMT"}
            )
        if calls == 2:
            assert request.headers["If-None-Match"] == '"v1"'
            assert request.headers["If-Modified-Since"] == "Wed, 25 Nov 2026 10:00:00 GMT"
            return httpx2.Response(200, stream=pending, headers={"ETag": '"v2"'})
        if calls == 3:
            assert request.headers["If-None-Match"] == '"v2"'
            assert "If-Modified-Since" not in request.headers
            return httpx2.Response(304)
        return httpx2.Response(200, content=b"999\nmalformed\n")

    async with FakeAsyncRedis() as redis, httpx2.AsyncClient(transport=httpx2.MockTransport(export)) as http:
        provider = cas.CasProvider(redis, export_url=EXPORT_URL, client=http)
        await provider.sync_bulk()
        previous = await redis.hget(provider.mirror_key, "set")
        task = asyncio.create_task(provider.sync_bulk())
        try:
            await asyncio.wait_for(pending.staged.wait(), timeout=2)
            assert (await provider.check_user(101)).matched
            assert not (await provider.check_user(202)).matched
        finally:
            pending.resume.set()
            await task
        current = await redis.hget(provider.mirror_key, "set")
        assert previous is not None and current is not None and current != previous
        assert not await redis.exists(previous)
        assert not (await provider.check_user(101)).matched
        assert (await provider.check_user(202)).matched
        assert (await provider.check_user(203)).matched
        await provider.sync_bulk()
        await provider.sync_bulk()
        assert await redis.hget(provider.mirror_key, "set") == current
        assert not (await provider.check_user(999)).matched


async def test_cas_api_fallback_is_authenticated_cached_and_enriches_mirror_hits() -> None:
    checked: list[str] = []

    def upstream(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == EXPORT_URL:
            assert "Authorization" not in request.headers
            return httpx2.Response(200, text="123\n")
        assert request.headers["Authorization"] == "Bearer test-key"
        user_id = request.url.params["user_id"]
        checked.append(user_id)
        if user_id == "234":
            return httpx2.Response(404, json={"ok": False, "error_code": 404})
        if user_id == "345":
            return httpx2.Response(503, json={"ok": False})
        return httpx2.Response(200, json={"ok": True, "result": {"reason": "Spam reports", "offenses": 3}})

    async with FakeAsyncRedis() as redis, httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)) as http:
        provider = cas.CasProvider(redis, export_url=EXPORT_URL, client=http, api_key="test-key")
        match = await provider.check_user(123)
        assert match.matched and match.reason == "Spam reports" and match.raw_metadata["offenses"] == 3
        assert match.reference_url == "https://cas.chat/query?u=123"
        with pytest.raises(TypeError):
            match.raw_metadata["offenses"] = 8
        for _attempt in range(2):
            clean = await provider.check_user(234)
            assert not clean.matched and clean.reference_url == "https://cas.chat/query?u=234"
        with pytest.raises(ProtectionUnavailable):
            await provider.check_user(345)
        await provider.sync_bulk()
        assert (await provider.check_user(123)).reason == "Spam reports"
        assert not (await provider.check_user(345)).matched
        assert checked == ["123", "234", "345"]


def entity_result() -> dict[str, object]:
    return {
        "entity_record": {
            "uuid": "entity",
            "hash": "hash",
            "host": "telegram.org",
            "whitelisted": False,
            "reputation": 0,
            "created": 0,
            "updated": 0,
        },
        "related_entities": [],
        "active_blacklists": [
            {
                "uuid": "old",
                "operator": "op",
                "entity": "entity",
                "type": "SCAM",
                "lifted": False,
                "created": 0,
                "expires": int(time()) - 60,
            },
            {"uuid": "current", "operator": "op", "entity": "entity", "type": "SPAM", "lifted": False, "created": 0},
        ],
        "suggested_action": "TEMPORARILY_BLOCK_ENTITY",
    }


async def test_ofd_entity_lookup_and_content_scan_normalize_and_cache() -> None:
    calls: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            assert request.url.path == "/entities/123@telegram.org/query"
            return httpx.Response(200, json=entity_result())
        assert request.url.path == "/scan"
        assert b'"author":"123@telegram.org"' in request.content
        assert b'"text_content":"watch out"' in request.content
        return httpx.Response(
            200,
            json={
                "resolved_entities": [],
                "classification": {"classification_flag": "MALICIOUS", "confidence": 0.95},
                "scan_results": {},
                "risk_score": 0.92,
                "detections": [{"concern": "PHISHING", "content_index": 0, "source": "test", "verified": True}],
            },
        )

    async with FakeAsyncRedis() as redis, httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        config = OfdServerConfig(id="alpha", name="Alpha", url="https://ofd.example", web_url="https://appeals.example")
        provider = OfdProvider(redis, config, client=http)
        for _attempt in range(2):
            lookup = await provider.check_user(123)
            assert lookup.matched and lookup.categories == (ThreatCategory.SPAM,)
            assert lookup.reference_url == "https://appeals.example/"
            scan = await provider.scan_content("watch out", 123, chat_tid=-10001)
            assert scan.matched and scan.categories == (ThreatCategory.PHISHING,) and scan.risk_score == 0.92
        await provider.scan_content("watch out", 123, chat_tid=-10002)
        assert len(calls) == 3


@pytest.mark.parametrize("status", [429, 503, 500])
async def test_ofd_circuit_fails_open_without_sleeping_or_caching_errors(status: int) -> None:
    calls: list[int] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(status)
        assert request.method == "GET"
        if len(calls) == 1:
            return httpx.Response(status, headers={"Retry-After": "45"}, json={"message": "busy"})
        return httpx.Response(404, json={"message": "Unknown entity"})

    async with FakeAsyncRedis() as redis, httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        provider = OfdProvider(redis, OfdServerConfig(id="ofd", name="OFD", url="https://ofd.example"), client=http)
        with pytest.raises(ProtectionUnavailable):
            await provider.check_user(123)
        key = f"{provider.key_prefix}:circuit"
        assert await redis.ttl(key) == (30 if status == 500 else 45)
        with pytest.raises(ProtectionUnavailable):
            await provider.scan_content("no request", 123, chat_tid=-10001)
        assert calls == [status]
        await redis.delete(key)
        assert not (await provider.check_user(123)).matched
        assert not (await provider.check_user(123)).matched
        assert calls == [status, status]


async def test_antispam_flat_env_config_registers_cas_and_ofd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTISPAM_CAS_API_KEY", "cas-secret")
    monkeypatch.setenv("ANTISPAM_OFD_URL", "https://ofd.example")
    monkeypatch.setenv("ANTISPAM_OFD_API_KEY", "ofd-secret")
    monkeypatch.setenv("ANTISPAM_OFD_NAME", "Primary OFD")
    monkeypatch.setenv("ANTISPAM_OFD_WEB_URL", "https://appeals.example")

    config = Config(_env_file=None)
    assert config.antispam_cas_export_url is None
    assert config.antispam_cas_api_key is not None
    assert config.antispam_cas_api_key.get_secret_value() == "cas-secret"
    assert len(config.resolved_antispam_ofd_servers) == 1
    server = config.resolved_antispam_ofd_servers[0]
    assert server.id == "default"
    assert server.name == "Primary OFD"
    assert str(server.url) == "https://ofd.example/"
    assert server.access_token is not None
    assert server.access_token.get_secret_value() == "ofd-secret"
    assert str(server.web_url) == "https://appeals.example/"

    async with FakeAsyncRedis() as redis:
        registry = ProviderRegistry(config, redis)
        try:
            cas_provider = registry.providers["cas"]
            ofd_provider = registry.providers["ofd:default"]
            assert isinstance(cas_provider, cas.CasProvider)
            assert cas_provider.export_url is None
            assert cas_provider.api_key == "cas-secret"
            assert isinstance(ofd_provider, OfdProvider)
            assert ofd_provider.display_name == "Primary OFD"
        finally:
            await registry.close()


def test_antispam_blank_env_strings_normalize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in (
        "ANTISPAM_CAS_API_KEY",
        "ANTISPAM_CAS_EXPORT_URL",
        "ANTISPAM_OFD_URL",
        "ANTISPAM_OFD_API_KEY",
        "ANTISPAM_OFD_WEB_URL",
    ):
        monkeypatch.setenv(env_name, "   ")

    config = Config(_env_file=None)
    assert config.antispam_cas_api_key is None
    assert config.antispam_cas_export_url is None
    assert config.antispam_ofd_url is None
    assert config.antispam_ofd_api_key is None
    assert config.antispam_ofd_web_url is None
    assert config.resolved_antispam_ofd_servers == ()


def test_antispam_rejects_duplicate_ofd_id_across_flat_and_list() -> None:
    with pytest.raises(ValidationError, match="OFD server IDs must be unique"):
        Config(
            _env_file=None,
            antispam_ofd_url="https://ofd-a.example",
            antispam_ofd_id="primary",
            antispam_ofd_servers=[OfdServerConfig(id="primary", name="Other", url="https://ofd-b.example")],
        )
