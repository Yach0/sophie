from __future__ import annotations

from typing import Any

from sophie_bot.db.models.global_user_whitelist import GlobalUserWhitelistModel
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted


async def test_global_whitelist_add_check_remove_is_idempotent(db_init: Any) -> None:
    del db_init
    user_tid = 700_000_001

    assert await is_user_globally_whitelisted(user_tid) is False
    assert await GlobalUserWhitelistModel.add_user(user_tid) is True
    assert await GlobalUserWhitelistModel.add_user(user_tid) is False
    assert await is_user_globally_whitelisted(user_tid) is True
    assert await GlobalUserWhitelistModel.remove_user(user_tid) is True
    assert await GlobalUserWhitelistModel.remove_user(user_tid) is False
    assert await is_user_globally_whitelisted(user_tid) is False

