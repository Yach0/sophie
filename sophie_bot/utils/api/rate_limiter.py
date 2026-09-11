from typing import cast

import structlog
from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from sophie_bot.config import CONFIG
from sophie_bot.services.application import ApplicationServices

log = structlog.get_logger(__name__)


def get_client_ip(request: Request) -> str:
    """Extract client IP, respecting reverse proxy headers only from trusted proxies."""
    direct_ip = request.client.host if request.client else None
    if direct_ip in CONFIG.trusted_proxies:
        if real_ip := request.headers.get("x-real-ip"):
            return real_ip.strip()
        if forwarded_for := request.headers.get("x-forwarded-for"):
            return forwarded_for.split(",")[0].strip()
    return direct_ip or "unknown"


async def rate_limit(request: Request, limit: int = 100, window: int = 60) -> None:
    """
    Rate limit requests by IP address.

    Args:
        request: The incoming request
        limit: Maximum number of requests allowed in the window (default: 100)
        window: Time window in seconds (default: 60)
    """
    redis = cast(ApplicationServices, request.app.state.services).redis
    client_ip = get_client_ip(request)
    key = f"rate_limit:{request.url.path}:{client_ip}"

    try:
        async with redis.pipeline() as pipe:
            pipe.incr(key)
            # NX: only set the TTL when the counter has none, so a client that keeps
            # sending cannot push the window's expiry back and lock itself out forever.
            pipe.expire(key, window, nx=True)
            results = await pipe.execute()
    except RedisError:
        # If Redis is unavailable, allow the request through rather than
        # returning 500 errors. Consistent with global rate limiter fail-open
        # design: availability over strict rate enforcement.
        log.exception(
            "Per-endpoint rate limiter Redis error, allowing request through",
            path=request.url.path,
            client_ip=client_ip,
        )
        return

    current_count = results[0]
    if current_count > limit:
        try:
            ttl = await redis.ttl(key)
        except RedisError:
            log.exception(
                "Per-endpoint rate limiter Redis error while reading TTL, allowing request through",
                path=request.url.path,
                client_ip=client_ip,
            )
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(max(ttl, 1))},
        )
