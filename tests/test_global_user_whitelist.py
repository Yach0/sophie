from __future__ import annotations

from typing import Any

from sophie_bot.db.models.global_user_whitelist import GlobalUserWhitelistModel
from sophie_bot.utils.feature_flags import delete_override, set_enabled
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted


async def test_global_whitelist_is_ignored_when_feature_is_disabled(db_init: Any) -> None:
    del db_init
    user_tid = 700_000_000
    await GlobalUserWhitelistModel.add_user(user_tid)

    assert await is_user_globally_whitelisted(user_tid) is False


async def test_global_whitelist_add_check_remove_is_idempotent(db_init: Any) -> None:
    del db_init
    user_tid = 700_000_001
    await set_enabled("global_user_whitelist", True)

    try:
        assert await is_user_globally_whitelisted(user_tid) is False
        assert await GlobalUserWhitelistModel.add_user(user_tid) is True
        assert await GlobalUserWhitelistModel.add_user(user_tid) is False
        assert await is_user_globally_whitelisted(user_tid) is True
        assert await GlobalUserWhitelistModel.remove_user(user_tid) is True
        assert await GlobalUserWhitelistModel.remove_user(user_tid) is False
        assert await is_user_globally_whitelisted(user_tid) is False
    finally:
        await delete_override("global_user_whitelist")
