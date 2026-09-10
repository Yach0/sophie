from __future__ import annotations

from sophie_bot.db.models import ChatModel, WSUserModel


async def clear_pending_user(user_tid: int, group_tid: int) -> None:
    """Clear an unpassed Welcome Security record for a user in a group."""
    user = await ChatModel.get_by_tid(user_tid)
    group = await ChatModel.get_by_tid(group_tid)
    if user is None or group is None:
        return

    ws_user = await WSUserModel.is_user(user.iid, group.iid)
    if ws_user is not None and not ws_user.passed:
        await WSUserModel.remove_user(user.iid, group.iid)
