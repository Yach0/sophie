from sophie_bot.db.models.global_user_whitelist import GlobalUserWhitelistModel


async def is_user_globally_whitelisted(user_tid: int) -> bool:
    """Return whether a Telegram user is exempt from automated moderation globally."""

    entry = await GlobalUserWhitelistModel.find_one(GlobalUserWhitelistModel.user_tid == user_tid)
    return entry is not None
