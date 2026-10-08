from aiogram import Bot
from redis.asyncio import Redis

from sophie_bot.db.models import ChatModel, WSUserModel
from sophie_bot.db.models.greetings import WelcomeMute
from sophie_bot.modules.restrictions.utils.restrictions import (
    execute_restriction,
)
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.welcomesecurity.utils_.db_time_convert import (
    convert_timedelta_or_str,
)
from sophie_bot.modules.welcomesecurity.utils_.welcomemute import on_welcomemute
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils.group_whitelist import is_user_group_whitelisted
from sophie_bot.utils.group_whitelist_logging import log_group_whitelist_exemption


async def ws_on_user_passed(
    user: ChatModel,
    group: ChatModel,
    welcomemute: WelcomeMute,
    *,
    bot: Bot,
    redis: Redis,
    pending: WSUserModel | None = None,
) -> bool:
    """
    Function when user successfully passed the welcomesecurity
    Returns whenever the user was unmuted.
    """

    if pending is None:
        pending = await WSUserModel.is_user(user.iid, group.iid)
        if pending is None:
            return False
        if pending.transition is None:
            pending = await pending.claim_transition("completing")
    if pending is None or pending.transition != "completing" or not await pending.transition_is_current():
        return False

    # Check for admin permissions
    if await is_user_admin(chat=group.tid, user=user.tid):
        result = await execute_restriction(
            bot,
            RestrictionAction.UNMUTE,
            group.tid,
            user.tid,
            is_current=pending.transition_is_current,
        )
        if result.applied:
            await pending.finish_transition()
        return result.applied

    # Unmute / restrict user
    if await is_user_group_whitelisted(group.tid, user.tid, redis=redis):
        await log_group_whitelist_exemption(group.tid, user.tid, "welcome_security_welcome_mute")
        result = await execute_restriction(
            bot,
            RestrictionAction.UNMUTE,
            group.tid,
            user.tid,
            is_current=pending.transition_is_current,
        )
        restriction_succeeded = result.applied
    elif welcomemute.enabled and welcomemute.time:
        restriction_succeeded = await on_welcomemute(
            group.tid,
            user.tid,
            is_current=pending.transition_is_current,
            on_time=convert_timedelta_or_str(welcomemute.time),
            bot=bot,
            redis=redis,
        )
    else:
        result = await execute_restriction(
            bot,
            RestrictionAction.UNMUTE,
            group.tid,
            user.tid,
            is_current=pending.transition_is_current,
        )
        restriction_succeeded = result.applied

    # Keep the pending record until the old CAPTCHA mute has been released or
    # replaced with the configured welcome restriction.
    if restriction_succeeded:
        await pending.finish_transition()

    return restriction_succeeded
