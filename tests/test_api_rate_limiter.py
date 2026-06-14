from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sophie_bot.utils.api import rate_limiter
from sophie_bot.utils.api.rate_limiter import get_client_ip, rate_limit


class FakePipeline:
    def __init__(self, execute_result: list[int] | None = None, execute_error: Exception | None = None) -> None:
        self.execute_result = execute_result or [1]
        self.execute_error = execute_error
        self.incr_keys: list[str] = []
        self.expire_calls: list[tuple[str, int]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self.incr_keys.append(key)

    def expire(self, key: str, window: int) -> None:
        self.expire_calls.append((key, window))

    async def execute(self) -> list[int]:
        if self.execute_error:
            raise self.execute_error
        return self.execute_result


class FakeRedis:
    def __init__(self, pipeline: FakePipeline, ttl_result: int = 30, ttl_error: Exception | None = None) -> None:
        self.pipeline_instance = pipeline
        self.ttl_result = ttl_result
        self.ttl_error = ttl_error
        self.ttl_keys: list[str] = []

    def pipeline(self) -> FakePipeline:
        return self.pipeline_instance

    async def ttl(self, key: str) -> int:
        self.ttl_keys.append(key)
        if self.ttl_error:
            raise self.ttl_error
        return self.ttl_result


def make_request(
    headers: dict[str, str] | None = None,
    client_host: str | None = "203.0.113.10",
    path: str = "/api/test",
) -> SimpleNamespace:
    client = SimpleNamespace(host=client_host) if client_host else None
    return SimpleNamespace(headers=headers or {}, client=client, url=SimpleNamespace(path=path))


def test_get_client_ip_prefers_real_ip_header() -> None:
    # Proxy headers are only trusted when the direct connection comes from a trusted proxy (127.0.0.1)
    request = make_request(headers={"x-real-ip": " 198.51.100.5 "}, client_host="127.0.0.1")

    assert get_client_ip(request) == "198.51.100.5"


def test_get_client_ip_ignores_real_ip_header_from_untrusted_client() -> None:
    # Headers from non-trusted IPs must not override the direct connection IP
    request = make_request(headers={"x-real-ip": " 198.51.100.5 "})

    assert get_client_ip(request) == "203.0.113.10"


def test_get_client_ip_uses_first_forwarded_for_value() -> None:
    # Proxy headers are only trusted when the direct connection comes from a trusted proxy (127.0.0.1)
    request = make_request(headers={"x-forwarded-for": "198.51.100.6, 198.51.100.7"}, client_host="127.0.0.1")

    assert get_client_ip(request) == "198.51.100.6"


def test_get_client_ip_ignores_forwarded_for_from_untrusted_client() -> None:
    # X-Forwarded-For from a non-trusted IP must not override the direct connection IP
    request = make_request(headers={"x-forwarded-for": "198.51.100.6, 198.51.100.7"})

    assert get_client_ip(request) == "203.0.113.10"


def test_get_client_ip_falls_back_to_unknown_without_client() -> None:
    request = make_request(client_host=None)

    assert get_client_ip(request) == "unknown"


@pytest.mark.asyncio
async def test_rate_limit_records_request_in_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(execute_result=[2])
    redis = FakeRedis(pipeline=pipeline)
    monkeypatch.setattr(rate_limiter, "aredis", redis)

    await rate_limit(make_request(), limit=3, window=45)

    expected_key = "rate_limit:/api/test:203.0.113.10"
    assert pipeline.incr_keys == [expected_key]
    assert pipeline.expire_calls == [(expected_key, 45)]
    assert redis.ttl_keys == []


@pytest.mark.asyncio
async def test_rate_limit_raises_with_retry_after_when_limit_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(execute_result=[4])
    redis = FakeRedis(pipeline=pipeline, ttl_result=12)
    monkeypatch.setattr(rate_limiter, "aredis", redis)

    with pytest.raises(HTTPException) as exc_info:
        await rate_limit(make_request(), limit=3, window=60)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many requests"
    assert exc_info.value.headers == {"Retry-After": "12"}
    assert redis.ttl_keys == ["rate_limit:/api/test:203.0.113.10"]


@pytest.mark.asyncio
async def test_rate_limit_fails_open_when_pipeline_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(execute_error=RuntimeError("redis unavailable"))
    redis = FakeRedis(pipeline=pipeline)
    monkeypatch.setattr(rate_limiter, "aredis", redis)

    await rate_limit(make_request(), limit=1, window=60)

    assert redis.ttl_keys == []


@pytest.mark.asyncio
async def test_rate_limit_fails_open_when_ttl_lookup_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(execute_result=[2])
    redis = FakeRedis(pipeline=pipeline, ttl_error=RuntimeError("ttl failed"))
    monkeypatch.setattr(rate_limiter, "aredis", redis)

    await rate_limit(make_request(), limit=1, window=60)

    assert redis.ttl_keys == ["rate_limit:/api/test:203.0.113.10"]
