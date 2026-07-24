from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from sophie_bot.config import CONFIG
from sophie_bot.services.i18n import i18n
from sophie_bot.services.redis import aredis
from sophie_bot.utils.api.rate_limiter import get_client_ip

logger = structlog.get_logger(__name__)

MAX_REQUEST_SIZE = 1_000_000  # 1MB default

# Paths exempt from global rate limiting (health checks, probes)
RATE_LIMIT_EXEMPT_PATHS: frozenset[str] = frozenset({"/health"})

# Global rate limit: requests per IP per window
GLOBAL_RATE_LIMIT = 300
GLOBAL_RATE_WINDOW = 60  # seconds


class I18nMiddleware(BaseHTTPMiddleware):
    """Middleware to set up i18n context for REST API requests."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        locale = CONFIG.default_locale

        accept_language = request.headers.get("accept-language")
        if accept_language:
            lang_code = accept_language.split(",")[0].split("-")[0]
            if lang_code in i18n.available_locales:
                locale = lang_code

        with i18n.context(), i18n.use_locale(locale):
            return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to limit request body size to prevent DoS attacks.

    Checks both the Content-Length header (fast path) and actual body size
    to prevent bypasses via chunked transfer encoding.
    """

    def __init__(self, app: ASGIApp, max_size: int = MAX_REQUEST_SIZE) -> None:
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_size:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header"},
                )
        elif request.method in ("POST", "PUT", "PATCH"):
            # No Content-Length header (chunked encoding) — read and check actual size
            body = await request.body()
            if len(body) > self.max_size:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )

        return await call_next(request)


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce a global per-IP rate limit across all endpoints.

    Design decision: fail-open on Redis outage.
    If Redis is unavailable, requests are allowed through rather than blocking
    all traffic. This is an intentional availability-over-security tradeoff
    appropriate for a Telegram bot API. Redis failures are logged and counted
    via the `global_rate_limit_redis_failures` metric so operators can set up
    alerts on sustained outages.
    """

    _redis_failure_count: int = 0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in RATE_LIMIT_EXEMPT_PATHS:
            return await call_next(request)

        client_ip = get_client_ip(request)
        key = f"global_rate_limit:{client_ip}"

        try:
            async with aredis.pipeline() as pipe:
                pipe.incr(key)
                # NX: only set the TTL when the counter has none, so a client that keeps
                # sending cannot push the window's expiry back and lock itself out forever.
                pipe.expire(key, GLOBAL_RATE_WINDOW, nx=True)
                results = await pipe.execute()

            current_count = results[0]
            if current_count > GLOBAL_RATE_LIMIT:
                ttl = await aredis.ttl(key)
                logger.warning(
                    "Global rate limit exceeded",
                    client_ip=client_ip,
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests"},
                    headers={"Retry-After": str(max(ttl, 1))},
                )
        except Exception:
            # Fail-open: allow the request through rather than blocking all
            # traffic. Track failure count for operator alerting.
            GlobalRateLimitMiddleware._redis_failure_count += 1
            logger.exception(
                "Global rate limiter Redis error, allowing request through",
                redis_failure_count=GlobalRateLimitMiddleware._redis_failure_count,
            )

        return await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="Sophie API")

    # I18n middleware
    app.add_middleware(I18nMiddleware)  # type: ignore[arg-type]

    # Security headers middleware
    app.add_middleware(SecurityHeadersMiddleware)  # type: ignore[arg-type]

    # Global rate limiting middleware
    app.add_middleware(GlobalRateLimitMiddleware)  # type: ignore[arg-type]

    # Request size limit middleware
    app.add_middleware(RequestSizeLimitMiddleware, max_size=MAX_REQUEST_SIZE)  # type: ignore[arg-type]

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,  # type: ignore[arg-type]
        allow_origins=CONFIG.api_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Accept-Language"],
    )

    return app


def init_api_routers(app: FastAPI, api_routers: Sequence[APIRouter]) -> None:
    for router in api_routers:
        app.include_router(router)
