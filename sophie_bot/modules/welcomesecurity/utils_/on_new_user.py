import asyncio
from collections.abc import Sequence

from sophie_bot.db.models import ChatModel, WSUserModel
from sophie_bot.modules.restrictions.utils.restrictions import mute_user
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted


async def ws_on_new_user(new_user: ChatModel, chat: ChatModel, is_join_request: bool = False) -> bool:
    """
    Function initializes welcomesecurity process internally.
    Returns whenever the user was muted.
    """

    if new_user.is_bot:
        return False

    # Admins and globally whitelisted users do not enter the captcha flow.
    if await is_user_globally_whitelisted(new_user.tid) or await is_user_admin(chat=chat.tid, user=new_user.tid):
        return False

    # Add user to the welcomesecurity database
    ws_user_db = await WSUserModel.ensure_user(new_user, chat, is_join_request)
    # False when the user already passed verification in this chat
    return not ws_user_db.passed


async def ws_on_new_user_mute(new_user: ChatModel, chat: ChatModel) -> bool:
    if await ws_on_new_user(new_user, chat):
        return await mute_user(chat_tid=chat.tid, user_tid=new_user.tid)
    return False


async def ws_on_new_users_mute(new_users: Sequence[ChatModel], chat: ChatModel) -> list[bool]:
    return await asyncio.gather(*(ws_on_new_user_mute(new_user, chat) for new_user in new_users))
