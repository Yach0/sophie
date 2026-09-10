from __future__ import annotations

import os

from redis.asyncio import Redis

from sophie_bot.config import Config


def create_redis(config: Config) -> Redis:
    """Create the application cache/state Redis client."""
    if os.environ.get("TESTING") == "1":
        from fakeredis import FakeAsyncRedis

        return FakeAsyncRedis(
            decode_responses=False,
            single_connection_client=True,
        )

    return Redis(
        host=config.redis_host,
        port=config.redis_port,
        username=config.redis_username,
        password=config.redis_password,
        db=config.redis_db_states,
        decode_responses=False,
        single_connection_client=True,
    )
