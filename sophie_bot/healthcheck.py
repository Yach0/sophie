"""Container healthcheck entrypoint: `python -m sophie_bot.healthcheck`.

Dispatches on `CONFIG.mode`. REST is probed over HTTP against its own /health
endpoint; the non-HTTP modes (bot, scheduler) are probed via the redis
heartbeat written by `sophie_bot.services.health`. Exit code 0 means healthy,
1 means unhealthy.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from sophie_bot.config import CONFIG
from sophie_bot.services.health import HEARTBEAT_TTL_SECONDS, check_heartbeat

HTTP_TIMEOUT_SECONDS = 5.0


async def _check_rest() -> tuple[bool, str]:
    url = f"http://{CONFIG.api_listen}:{CONFIG.api_port}/health"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        return False, f"rest: request failed: {error}"

    if response.status_code != 200:
        return False, f"rest: HTTP {response.status_code}"

    try:
        payload = response.json()
    except ValueError:
        return False, "rest: invalid JSON body"

    status = payload.get("status")
    if status != "ok":
        return False, f"rest: status={status!r}"

    return True, "rest: ok"


async def _check_heartbeat_mode(component: str) -> tuple[bool, str]:
    if await check_heartbeat(component, HEARTBEAT_TTL_SECONDS):
        return True, f"{component}: heartbeat fresh"
    return False, f"{component}: heartbeat missing or stale"


async def _run() -> tuple[bool, str]:
    mode = CONFIG.mode
    if mode == "rest":
        return await _check_rest()
    if mode in ("bot", "scheduler"):
        return await _check_heartbeat_mode(mode)
    return False, f"{mode}: no healthcheck defined"


def main() -> int:
    healthy, status = asyncio.run(_run())
    print(status)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
