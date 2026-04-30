from __future__ import annotations

from redis.asyncio import Redis

from sophie_bot.services.registry import registry

# Backward-compatible module-level variable.
# Delegates to the registry which uses os.environ.get("TESTING") to decide
# between FakeAsyncRedis and a real Redis connection.
aredis: Redis = registry.get_redis()
