import structlog
from fastapi import HTTPException, Request, status

from sophie_bot.services.redis import aredis

log = structlog.get_logger(__name__)


def get_client_ip(request: Request) -> str:
    """Extract client IP, respecting reverse proxy headers.

    Checks X-Real-IP first (set by nginx/similar), then X-Forwarded-For
    (first IP in the chain is the original client), then falls back to the
    direct connection address.
    """
    if real_ip := request.headers.get("x-real-ip"):
        return real_ip.strip()
    if forwarded_for := request.headers.get("x-forwarded-for"):
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request, limit: int = 100, window: int = 60) -> None:
    """
    Rate limit requests by IP address.

    Args:
        request: The incoming request
        limit: Maximum number of requests allowed in the window (default: 100)
        window: Time window in seconds (default: 60)
    """
    client_ip = get_client_ip(request)
    key = f"rate_limit:{request.url.path}:{client_ip}"

    try:
        async with aredis.pipeline() as pipe:
            await pipe.incr(key)
            await pipe.expire(key, window)
            results = await pipe.execute()
    except Exception:
        # If Redis is unavailable, allow the request through rather than
        # returning 500 errors. Consistent with global rate limiter fail-open
        # design: availability over strict rate enforcement.
        log.error(
            "Per-endpoint rate limiter Redis error, allowing request through",
            path=request.url.path,
            client_ip=client_ip,
            exc_info=True,
        )
        return

    current_count = results[0]
    if current_count > limit:
        try:
            ttl = await aredis.ttl(key)
        except Exception:
            log.error(
                "Per-endpoint rate limiter Redis error while reading TTL, allowing request through",
                path=request.url.path,
                client_ip=client_ip,
                exc_info=True,
            )
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(max(ttl, 1))},
        )
