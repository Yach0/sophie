from datetime import timedelta

from aiogram import Bot
from redis.asyncio import Redis

from sophie_bot.modules.restrictions.utils.restrictions import (
    execute_restriction,
)
from sophie_bot.modules.welcomesecurity.utils_.db_time_convert import (
    convert_timedelta_or_str,
)
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted


async def on_welcomemute(
    group_id: int,
    user_id: int,
    on_time: str | timedelta,
    *,
    bot: Bot,
    redis: Redis,
) -> bool:
    if await is_user_globally_whitelisted(user_id, redis=redis):
        return False
    return (
        await execute_restriction(
            bot,
            RestrictionAction.RESTRICT,
            group_id,
            user_id,
            until_date=convert_timedelta_or_str(on_time),
        )
    ).applied
