from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.utils.logger import log


async def log_group_whitelist_exemption(chat_tid: int, user_tid: int, subsystem: str) -> None:
    """Record an automated moderation exemption for an already-whitelisted user."""

    log.debug(
        "Group whitelist exemption",
        chat_tid=chat_tid,
        user_tid=user_tid,
        subsystem=subsystem,
    )
    await log_event(
        chat_tid,
        user_tid,
        LogEvent.GROUP_WHITELIST_EXEMPTION,
        {
            "subsystem": subsystem,
            "chat_tid": chat_tid,
            "user_tid": user_tid,
        },
    )
