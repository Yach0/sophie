from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sophie_bot.modules.federations.utils.ban_docs import build_ban_log_doc, build_ban_reply_doc


@pytest.mark.parametrize(
    ("direct_count", "federation_count", "subscriber_chat_count", "total_chats", "counts"),
    [
        pytest.param(0, 0, 0, 0, "User banned in 0 out of 0 chats in the federation", id="empty"),
        pytest.param(0, 0, 0, 8, "User banned in 0 out of 8 chats in the federation", id="zero-of-eight"),
        pytest.param(1, 0, 0, 1, "User banned in 1 out of 1 chat in the federation", id="direct-singular"),
        pytest.param(1, 0, 0, 8, "User banned in 1 out of 8 chats in the federation", id="partial-singular-success"),
        pytest.param(2, 0, 0, 4, "User banned in 2 out of 4 chats in the federation", id="direct-plural"),
        pytest.param(
            0,
            1,
            0,
            8,
            "User banned in 0 out of 8 chats in the federation\nUser banned in 0 chats in subscribed federations",
            id="subscribed-zero",
        ),
        pytest.param(
            0,
            1,
            1,
            8,
            "User banned in 0 out of 8 chats in the federation\nUser banned in 1 chat in subscribed federations",
            id="subscribed-singular",
        ),
        pytest.param(
            2,
            2,
            3,
            4,
            "User banned in 2 out of 4 chats in the federation\nUser banned in 3 chats in subscribed federations",
            id="mixed-plural",
        ),
    ],
)
def test_ban_reply_and_log_have_exact_harmonized_presentation(
    direct_count: int,
    federation_count: int,
    subscriber_chat_count: int,
    total_chats: int,
    counts: str,
) -> None:
    federation = MagicMock(fed_name="Fed <&>", fed_id="fed<&>")
    user = MagicMock(tid=123456789, first_name_or_title="Example User <&>")
    reply = build_ban_reply_doc(
        federation,
        user,
        456,
        "Admin <&>",
        "Reason <&>",
        False,
        banned_count=direct_count,
        total_chats=total_chats,
        lazy_ban_count=federation_count,
        subscribed_banned_count=subscriber_chat_count,
    ).to_html()
    log = build_ban_log_doc(
        federation,
        user,
        456,
        "Admin <&>",
        direct_count,
        total_chats,
        "Reason <&>",
        "Original <&>",
        subscribed_banned_count=subscriber_chat_count,
        subscribed_federation_count=federation_count,
    ).to_html()

    title = "<b>[🏛 User Banned from Federation]</b>"
    assert reply.splitlines()[0] == title
    assert log.splitlines()[0] == "<b>[🏛 User Banned from Federation #FedBan]</b>"
    assert reply == (
        title + "\n"
        "<b>Federation</b>: Fed &lt;&amp;&gt;\n"
        '<b>User</b>: <a href="tg://user?id=123456789">Example User &lt;&amp;&gt;</a> (<code>123456789</code>)\n'
        '<b>Banned by</b>: <a href="tg://user?id=456">Admin &lt;&amp;&gt;</a>\n'
        "<b>Reason</b>: <code>Reason &lt;&amp;&gt;</code>\n" + counts
    )
    assert log == (
        "<b>[🏛 User Banned from Federation #FedBan]</b>\n"
        "<b>Federation</b>: Fed &lt;&amp;&gt; (fed&lt;&amp;&gt;)\n"
        '<b>User</b>: <a href="tg://user?id=123456789">Example User &lt;&amp;&gt;</a> (<code>123456789</code>)\n'
        '<b>Banned by</b>: <a href="tg://user?id=456">Admin &lt;&amp;&gt;</a>\n'
        "<b>Reason</b>: <code>Reason &lt;&amp;&gt;</code>\n" + counts + "\n"
        "<u>Original message</u>:\n"
        "  <tg-spoiler>Original &lt;&amp;&gt;</tg-spoiler>"
    )
    for rendered in (reply, log):
        assert "Result" not in rendered
        assert "Also banned in" not in rendered
        assert "Banned in" not in rendered
        assert "Federation chats" not in rendered
        assert "Subscribed federations" not in rendered
        assert "Subscribed-federation chats" not in rendered
        assert rendered.count("<code>123456789</code>") == 1
        for unsafe in ("Fed <&>", "fed<&>", "User <&>", "Admin <&>", "Reason <&>", "Original <&>"):
            assert unsafe not in rendered
    for audit_detail in ("#FedBan", "fed&lt;&amp;&gt;", "Original message"):
        assert audit_detail not in reply
        assert audit_detail in log


@pytest.mark.parametrize("immediate_chat_banned", [False, True])
def test_progress_reply_does_not_claim_final_counts(immediate_chat_banned: bool) -> None:
    rendered = build_ban_reply_doc(
        MagicMock(fed_name="Fed"),
        MagicMock(tid=123, first_name_or_title="User"),
        456,
        "Admin",
        None,
        True,
        banned_count=2,
        lazy_ban_count=1,
        subscribed_banned_count=3,
        propagating=True,
        immediate_chat_banned=immediate_chat_banned,
        banner_anonymous=True,
    ).to_html()
    assert "Propagating across the federation" in rendered
    assert ("Banned in this chat" in rendered) == immediate_chat_banned
    assert "Result" not in rendered
    assert "Also banned in" not in rendered
    assert "Anonymous admin" in rendered
    assert "tg://user?id=456" not in rendered
    assert "The action is silent" in rendered
    assert "Federation chats" not in rendered
    assert "Subscribed" not in rendered
    assert "User banned in" not in rendered


def test_final_reply_defaults_and_optional_log_details() -> None:
    federation = MagicMock(fed_name="Fed", fed_id="fed")
    user = MagicMock(tid=123, first_name_or_title=None)
    reply = build_ban_reply_doc(
        federation, user, 456, "Admin", None, False, total_chats=8, banner_anonymous=True
    ).to_html()
    log = build_ban_log_doc(federation, user, 456, "Admin", 0, 1, None, None).to_html()
    assert reply == (
        "<b>[🏛 User Banned from Federation]</b>\n"
        "<b>Federation</b>: Fed\n"
        '<b>User</b>: <a href="tg://user?id=123">Unknown</a> (<code>123</code>)\n'
        "<b>Banned by</b>: Anonymous admin\n"
        "User banned in 0 out of 8 chats in the federation"
    )
    assert log == (
        "<b>[🏛 User Banned from Federation #FedBan]</b>\n"
        "<b>Federation</b>: Fed (fed)\n"
        '<b>User</b>: <a href="tg://user?id=123">Unknown</a> (<code>123</code>)\n'
        '<b>Banned by</b>: <a href="tg://user?id=456">Admin</a>\n'
        "User banned in 0 out of 1 chat in the federation"
    )
