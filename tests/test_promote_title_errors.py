"""Regression tests for the /promote admin-title error handling (SOPHIE-27V)."""

from sophie_bot.modules.promotes.handlers.promote import PROMOTE_PERMISSIONS, tolerated_title_errors
from sophie_bot.modules.utils_.telegram_exceptions import NOT_ENOUGH_RIGHTS, RIGHT_FORBIDDEN, USER_NOT_ADMIN


def test_user_not_admin_is_tolerated_when_rights_were_granted() -> None:
    """The real race: promote_chat_member succeeded but Telegram has not committed it yet."""
    granted = {perm: True for perm in PROMOTE_PERMISSIONS}

    assert USER_NOT_ADMIN in tolerated_title_errors(granted)


def test_user_not_admin_surfaces_when_no_rights_were_granted() -> None:
    """With every right false, promote_chat_member is a no-op, so the user is genuinely not an admin.

    Swallowing it here would make the handler report a successful promotion that never happened.
    """
    granted = {perm: False for perm in PROMOTE_PERMISSIONS}

    assert USER_NOT_ADMIN not in tolerated_title_errors(granted)


def test_permission_errors_are_always_tolerated() -> None:
    for granted in ({perm: True for perm in PROMOTE_PERMISSIONS}, {perm: False for perm in PROMOTE_PERMISSIONS}):
        tolerated = tolerated_title_errors(granted)
        assert RIGHT_FORBIDDEN in tolerated
        assert NOT_ENOUGH_RIGHTS in tolerated
