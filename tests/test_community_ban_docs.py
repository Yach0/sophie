from __future__ import annotations

from unittest.mock import MagicMock

from sophie_bot.modules.communities.utils.ban_docs import (
    build_ban_reply_doc as build_community_ban_reply_doc,
)
from sophie_bot.modules.communities.utils.ban_docs import (
    build_unban_reply_doc as build_community_unban_reply_doc,
)
from sophie_bot.modules.federations.utils.ban_docs import (
    build_ban_log_doc,
)
from sophie_bot.modules.federations.utils.ban_docs import (
    build_ban_reply_doc as build_federation_ban_reply_doc,
)
from sophie_bot.modules.federations.utils.ban_docs import (
    build_unban_reply_doc as build_federation_unban_reply_doc,
)


def _build_user() -> MagicMock:
    return MagicMock(tid=123, first_name_or_title="Test user")


def _build_community() -> MagicMock:
    community = MagicMock()
    community.name = "Test community"
    return community


def _build_federation() -> MagicMock:
    return MagicMock(fed_name="Test federation", fed_id="test-fed")


def _render_community_ban_result(banned_count: int) -> str:
    return str(
        build_community_ban_reply_doc(
            _build_community(),
            _build_user(),
            banner_tid=456,
            banner_name="Test moderator",
            reason=None,
            silent=False,
            banned_count=banned_count,
        )
    )


def _render_community_unban_result(unbanned_count: int) -> str:
    return str(
        build_community_unban_reply_doc(
            _build_community(),
            _build_user(),
            unbanner_tid=456,
            unbanner_name="Test moderator",
            unbanned_count=unbanned_count,
        )
    )


def _render_federation_ban_result(banned_count: int, lazy_ban_count: int = 0) -> str:
    federation = _build_federation()
    return str(
        build_federation_ban_reply_doc(
            federation,
            _build_user(),
            banner_tid=456,
            banner_name="Test moderator",
            reason=None,
            silent=False,
            banned_count=banned_count,
            lazy_ban_count=lazy_ban_count,
        )
    )


def _render_federation_unban_result(unbanned_count: int) -> str:
    federation = _build_federation()
    return str(
        build_federation_unban_reply_doc(
            federation,
            _build_user(),
            unbanner_tid=456,
            unbanner_name="Test moderator",
            unbanned_count=unbanned_count,
        )
    )


def test_community_ban_result_uses_singular_for_one_chat() -> None:
    rendered = _render_community_ban_result(1)

    assert "Banned in <code>1</code> chat" in rendered
    assert "Banned in <code>1</code> chats" not in rendered


def test_community_ban_result_uses_plural_for_multiple_chats() -> None:
    rendered = _render_community_ban_result(2)

    assert "Banned in <code>2</code> chats" in rendered


def test_community_unban_result_uses_singular_for_one_chat() -> None:
    rendered = _render_community_unban_result(1)

    assert "Unbanned in <code>1</code> chat" in rendered


def test_federation_ban_result_pluralizes_chat_and_subscribed_federation_counts() -> None:
    rendered = _render_federation_ban_result(1, lazy_ban_count=1)

    assert "Banned in <code>1</code> chat" in rendered
    assert "<code>1</code> subscribed federation" in rendered


def test_federation_unban_result_uses_plural_for_multiple_chats() -> None:
    rendered = _render_federation_unban_result(2)

    assert "Unbanned in <code>2</code> chats" in rendered


def test_federation_ban_log_pluralizes_total_chat_count() -> None:
    federation = _build_federation()
    rendered = str(
        build_ban_log_doc(
            federation,
            _build_user(),
            banner_name="Test moderator",
            banned_count=1,
            total_chats=1,
            reason=None,
            original_message_text=None,
        )
    )

    assert "1 out of 1 chat in the federation" in rendered
    assert "1 out of 1 chats in the federation" not in rendered
