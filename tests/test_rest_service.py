from contextlib import contextmanager
from types import SimpleNamespace
from typing import Iterator

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import APIRouter
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from sophie_bot.services import rest
from sophie_bot.services.rest import (
    GLOBAL_RATE_LIMIT,
    GLOBAL_RATE_WINDOW,
    GlobalRateLimitMiddleware,
    I18nMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    create_app,
    init_api_routers,
)


async def dummy_app(scope: Scope, receive: Receive, send: Send) -> None:
    return None


def make_request(
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: bytes = b"",
    path: str = "/api/test",
) -> SimpleNamespace:
    async def request_body() -> bytes:
        return body

    return SimpleNamespace(
        headers=headers or {},
        method=method,
        body=request_body,
        url=SimpleNamespace(path=path),
        client=SimpleNamespace(host="203.0.113.55"),
    )


async def ok_response(request: object) -> Response:
    return Response("ok")


@pytest.mark.asyncio
async def test_request_size_limit_rejects_large_content_length() -> None:
    middleware = RequestSizeLimitMiddleware(dummy_app, max_size=5)

    response = await middleware.dispatch(make_request(headers={"content-length": "6"}), ok_response)

    assert response.status_code == 413
    assert response.body == b'{"detail":"Request body too large"}'


@pytest.mark.asyncio
async def test_request_size_limit_rejects_invalid_content_length() -> None:
    middleware = RequestSizeLimitMiddleware(dummy_app, max_size=5)

    response = await middleware.dispatch(make_request(headers={"content-length": "invalid"}), ok_response)

    assert response.status_code == 400
    assert response.body == b'{"detail":"Invalid Content-Length header"}'


@pytest.mark.asyncio
async def test_request_size_limit_checks_chunked_body_size() -> None:
    middleware = RequestSizeLimitMiddleware(dummy_app, max_size=5)

    response = await middleware.dispatch(make_request(method="POST", body=b"too-large"), ok_response)

    assert response.status_code == 413
    assert response.body == b'{"detail":"Request body too large"}'


@pytest.mark.asyncio
async def test_request_size_limit_allows_small_requests() -> None:
    middleware = RequestSizeLimitMiddleware(dummy_app, max_size=5)

    response = await middleware.dispatch(make_request(method="POST", body=b"small"), ok_response)

    assert response.status_code == 200
    assert response.body == b"ok"


@pytest.mark.asyncio
async def test_security_headers_middleware_adds_hardening_headers() -> None:
    middleware = SecurityHeadersMiddleware(dummy_app)

    response = await middleware.dispatch(make_request(), ok_response)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"


class FakeI18n:
    available_locales = {"en", "uk"}

    def __init__(self) -> None:
        self.used_locales: list[str] = []

    @contextmanager
    def context(self) -> Iterator[None]:
        yield

    @contextmanager
    def use_locale(self, locale: str) -> Iterator[None]:
        self.used_locales.append(locale)
        yield


@pytest.mark.asyncio
async def test_i18n_middleware_uses_supported_accept_language(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_i18n = FakeI18n()
    middleware = I18nMiddleware(dummy_app)
    monkeypatch.setattr(rest, "i18n", fake_i18n)
    monkeypatch.setattr(rest.CONFIG, "default_locale", "en")

    response = await middleware.dispatch(make_request(headers={"accept-language": "uk-UA, en;q=0.8"}), ok_response)

    assert response.status_code == 200
    assert fake_i18n.used_locales == ["uk"]


class FakePipeline:
    def __init__(self, current_count: int, execute_error: Exception | None = None) -> None:
        self.current_count = current_count
        self.execute_error = execute_error
        self.incr_keys: list[str] = []
        self.expire_calls: list[tuple[str, int, bool]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self.incr_keys.append(key)

    def expire(self, key: str, window: int, nx: bool = False) -> None:
        self.expire_calls.append((key, window, nx))

    async def execute(self) -> list[int]:
        if self.execute_error:
            raise self.execute_error
        return [self.current_count]


class FakeRedis:
    def __init__(self, pipeline: FakePipeline, ttl_result: int = 30) -> None:
        self.pipeline_instance = pipeline
        self.ttl_result = ttl_result
        self.ttl_keys: list[str] = []

    def pipeline(self) -> FakePipeline:
        return self.pipeline_instance

    async def ttl(self, key: str) -> int:
        self.ttl_keys.append(key)
        return self.ttl_result


@pytest.mark.asyncio
async def test_global_rate_limit_skips_exempt_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(current_count=GLOBAL_RATE_LIMIT + 1)
    fake_redis = FakeRedis(pipeline)
    middleware = GlobalRateLimitMiddleware(dummy_app)
    monkeypatch.setattr(rest, "aredis", fake_redis)

    response = await middleware.dispatch(make_request(path="/health"), ok_response)

    assert response.status_code == 200
    assert pipeline.incr_keys == []


@pytest.mark.asyncio
async def test_global_rate_limit_records_allowed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(current_count=GLOBAL_RATE_LIMIT)
    fake_redis = FakeRedis(pipeline)
    middleware = GlobalRateLimitMiddleware(dummy_app)
    monkeypatch.setattr(rest, "aredis", fake_redis)

    response = await middleware.dispatch(make_request(), ok_response)

    expected_key = "global_rate_limit:203.0.113.55"
    assert response.status_code == 200
    assert pipeline.incr_keys == [expected_key]
    # nx=True: the TTL must only be set when the counter has none, never refreshed
    assert pipeline.expire_calls == [(expected_key, GLOBAL_RATE_WINDOW, True)]
    assert fake_redis.ttl_keys == []


@pytest.mark.asyncio
async def test_global_rate_limit_rejects_request_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(current_count=GLOBAL_RATE_LIMIT + 1)
    fake_redis = FakeRedis(pipeline, ttl_result=9)
    middleware = GlobalRateLimitMiddleware(dummy_app)
    monkeypatch.setattr(rest, "aredis", fake_redis)

    response = await middleware.dispatch(make_request(), ok_response)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "9"
    assert response.body == b'{"detail":"Too many requests"}'
    assert fake_redis.ttl_keys == ["global_rate_limit:203.0.113.55"]


@pytest.mark.asyncio
async def test_global_rate_limit_does_not_extend_window_on_subsequent_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = FakeAsyncRedis()
    middleware = GlobalRateLimitMiddleware(dummy_app)
    monkeypatch.setattr(rest, "aredis", fake_redis)
    key = "global_rate_limit:203.0.113.55"

    await middleware.dispatch(make_request(), ok_response)
    # fakeredis has no advanceable clock; shrinking the TTL stands in for elapsed time.
    await fake_redis.expire(key, 10)
    await middleware.dispatch(make_request(), ok_response)

    assert await fake_redis.ttl(key) <= 10
    await fake_redis.aclose()


@pytest.mark.asyncio
async def test_global_rate_limit_rejected_request_does_not_extend_window(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = FakeAsyncRedis()
    middleware = GlobalRateLimitMiddleware(dummy_app)
    monkeypatch.setattr(rest, "aredis", fake_redis)
    key = "global_rate_limit:203.0.113.55"

    await fake_redis.set(key, GLOBAL_RATE_LIMIT)
    await fake_redis.expire(key, 10)

    response = await middleware.dispatch(make_request(), ok_response)

    assert response.status_code == 429
    # A 429 must not push the window back, otherwise the client can never escape the lockout.
    assert await fake_redis.ttl(key) <= 10
    await fake_redis.aclose()


@pytest.mark.asyncio
async def test_global_rate_limit_fails_open_on_redis_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline(current_count=0, execute_error=RuntimeError("redis down"))
    fake_redis = FakeRedis(pipeline)
    middleware = GlobalRateLimitMiddleware(dummy_app)
    GlobalRateLimitMiddleware._redis_failure_count = 0
    monkeypatch.setattr(rest, "aredis", fake_redis)

    response = await middleware.dispatch(make_request(), ok_response)

    assert response.status_code == 200
    assert GlobalRateLimitMiddleware._redis_failure_count == 1


def test_create_app_adds_expected_middleware_and_init_api_routers_includes_router() -> None:
    app = create_app()
    router = APIRouter(prefix="/unit-test")

    @router.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    init_api_routers(app, [router])

    middleware_classes = {middleware.cls for middleware in app.user_middleware}
    assert I18nMiddleware in middleware_classes
    assert SecurityHeadersMiddleware in middleware_classes
    assert GlobalRateLimitMiddleware in middleware_classes
    assert RequestSizeLimitMiddleware in middleware_classes
    assert app.url_path_for("ping") == "/unit-test/ping"
