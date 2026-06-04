import asyncio
from unittest.mock import patch

from aiogram import Dispatcher

from sophie_bot.config import CONFIG
from sophie_bot.modules import load_modules
from sophie_bot.utils.logger import log


class RedisStub:
    async def hget(self, key: str, field: str) -> bytes | None:
        from sophie_bot.utils.feature_flags import FEATURE_FLAGS, _serialize_value, get_default_value

        if field in FEATURE_FLAGS:
            return _serialize_value(get_default_value(field)).encode()
        return None

    async def hset(self, key: str, field: str, value: str) -> int:
        return 0


# We need to patch the databases in order to be able to run this in CI without them.
@patch("redis.asyncio.Redis")
@patch("redis.StrictRedis")
def generate_wiki(mock_redis, mock_aredis):
    mock_aredis.return_value = RedisStub()
    mock_redis.return_value = RedisStub()

    log.info("Starting wiki generation task...")
    dp = Dispatcher()

    CONFIG.mode = "nostart"

    asyncio.run(load_modules(dp, ["*"], CONFIG.modules_not_load))

    from tools.wiki_gen.generate_pages import generate_wiki_pages

    asyncio.run(generate_wiki_pages())


if __name__ == "__main__":
    generate_wiki()
