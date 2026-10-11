import asyncio
from collections.abc import Sequence

from aiogram import Bot
from redis.asyncio import Redis

from sophie_bot.db.models import ChatModel, WSUserModel
from sophie_bot.modules.restrictions.utils.restrictions import (
    execute_restriction,
)
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils.group_whitelist import is_user_group_whitelisted
from sophie_bot.utils.group_whitelist_logging import log_group_whitelist_exemption


async def _initialize_pending_user(
    new_user: ChatModel,
    chat: ChatModel,
    is_join_request: bool = False,
    *,
    redis: Redis,
) -> WSUserModel | None:
    """
    Function initializes welcomesecurity process internally.
    Returns pending state; None means exempt or already passed.
    """

    if new_user.is_bot:
        return None

    if await is_user_group_whitelisted(chat.tid, new_user.tid, redis=redis):
        await log_group_whitelist_exemption(chat.tid, new_user.tid, "welcome_security_captcha")
        return None

    if await is_user_admin(chat=chat.tid, user=new_user.tid):
        return None

    # Add user to the welcomesecurity database
    ws_user_db = await WSUserModel.ensure_user(new_user, chat, is_join_request)
    # False when the user already passed verification in this chat
    return None if ws_user_db.passed else ws_user_db


async def ws_on_new_user(
    new_user: ChatModel,
    chat: ChatModel,
    is_join_request: bool = False,
    *,
    redis: Redis,
) -> bool | None:
    """False is exempt, True needs CAPTCHA, None retains a durable decision."""
    pending = await _initialize_pending_user(new_user, chat, is_join_request, redis=redis)
    if pending is None:
        return False
    return True if pending.transition is None else None


async def ws_on_new_user_mute(new_user: ChatModel, chat: ChatModel, *, bot: Bot, redis: Redis) -> bool:
    pending = await _initialize_pending_user(new_user, chat, redis=redis)
    if pending is None or pending.transition is not None:
        return False
    return (
        await execute_restriction(
            bot,
            RestrictionAction.MUTE,
            chat.tid,
            new_user.tid,
            is_current=pending.transition_is_current,
        )
    ).applied


async def ws_on_new_users_mute(
    new_users: Sequence[ChatModel],
    chat: ChatModel,
    *,
    bot: Bot,
    redis: Redis,
) -> list[bool]:
    return await asyncio.gather(*(ws_on_new_user_mute(new_user, chat, bot=bot, redis=redis) for new_user in new_users))
