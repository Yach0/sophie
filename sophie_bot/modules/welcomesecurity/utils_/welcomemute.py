from datetime import timedelta

from sophie_bot.modules.restrictions.utils.restrictions import restrict_user
from sophie_bot.modules.welcomesecurity.utils_.db_time_convert import (
    convert_timedelta_or_str,
)
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted


async def on_welcomemute(group_id: int, user_id: int, on_time: str | timedelta) -> bool:
    if await is_user_globally_whitelisted(user_id):
        return False
    return await restrict_user(
        group_id,
        user_id,
        until_date=convert_timedelta_or_str(on_time),
    )
