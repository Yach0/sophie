from datetime import timedelta

from aiogram import Bot

from sophie_bot.modules.restrictions.utils.restrictions import (
    execute_restriction,
)
from sophie_bot.modules.welcomesecurity.utils_.db_time_convert import (
    convert_timedelta_or_str,
)
from sophie_bot.shared.actions import RestrictionAction


async def on_welcomemute(
    group_id: int,
    user_id: int,
    on_time: str | timedelta,
    *,
    bot: Bot,
) -> bool:
    return (
        await execute_restriction(
            bot,
            RestrictionAction.RESTRICT,
            group_id,
            user_id,
            until_date=convert_timedelta_or_str(on_time),
        )
    ).applied
