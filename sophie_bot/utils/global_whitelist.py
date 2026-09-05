from sophie_bot.db.models.global_user_whitelist import GlobalUserWhitelistModel
from sophie_bot.utils.feature_flags import is_enabled


async def is_user_globally_whitelisted(user_tid: int) -> bool:
    """Return whether a Telegram user is exempt from automated moderation globally."""

    if not await is_enabled("global_user_whitelist"):
        return False

    entry = await GlobalUserWhitelistModel.find_one(GlobalUserWhitelistModel.user_tid == user_tid)
    return entry is not None
