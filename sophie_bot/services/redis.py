import os
import sys

from redis.asyncio import Redis

from sophie_bot.config import CONFIG

if "pytest" in sys.modules or os.environ.get("TESTING") == "1":
    from fakeredis import FakeAsyncRedis

    aredis: Redis = FakeAsyncRedis(
        decode_responses=False,
        single_connection_client=True,
    )
else:
    aredis: Redis = Redis(
        host=CONFIG.redis_host,
        port=CONFIG.redis_port,
        username=CONFIG.redis_username,
        password=CONFIG.redis_password,
        db=CONFIG.redis_db_states,
        decode_responses=False,
        single_connection_client=True,
    )
