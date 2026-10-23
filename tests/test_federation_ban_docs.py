from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sophie_bot.modules.federations.utils.ban_docs import build_ban_log_doc, build_ban_reply_doc


@pytest.mark.parametrize(
    ("direct_count", "federation_count", "subscriber_chat_count", "total_chats", "reply_counts", "log_counts"),
    [
        pytest.param(
            0,
            0,
            0,
            0,
            "<b>Federation chats</b>: <code>0</code>",
            "<b>Federation chats</b>: <code>0</code> out of <code>0</code>",
            id="zero",
        ),
        pytest.param(
            1,
            0,
            0,
            1,
            "<b>Federation chats</b>: <code>1</code>",
            "<b>Federation chats</b>: <code>1</code> out of <code>1</code>",
            id="direct-singular",
        ),
        pytest.param(
            2,
            0,
            0,
            4,
            "<b>Federation chats</b>: <code>2</code>",
            "<b>Federation chats</b>: <code>2</code> out of <code>4</code>",
            id="direct-plural-partial",
        ),
        pytest.param(
            0,
            1,
            0,
            1,
            "<b>Federation chats</b>: <code>0</code>\n"
            "<b>Subscribed federations</b>: <code>1</code>\n"
            "<b>Subscribed-federation chats</b>: <code>0</code>",
            "<b>Federation chats</b>: <code>0</code> out of <code>1</code>\n"
            "<b>Subscribed federations</b>: <code>1</code>\n"
            "<b>Subscribed-federation chats</b>: <code>0</code>",
            id="subscribed-zero-chats",
        ),
        pytest.param(
            0,
            1,
            1,
            1,
            "<b>Federation chats</b>: <code>0</code>\n"
            "<b>Subscribed federations</b>: <code>1</code>\n"
            "<b>Subscribed-federation chats</b>: <code>1</code>",
            "<b>Federation chats</b>: <code>0</code> out of <code>1</code>\n"
            "<b>Subscribed federations</b>: <code>1</code>\n"
            "<b>Subscribed-federation chats</b>: <code>1</code>",
            id="subscribed-singular",
        ),
        pytest.param(
            0,
            2,
            3,
            4,
            "<b>Federation chats</b>: <code>0</code>\n"
            "<b>Subscribed federations</b>: <code>2</code>\n"
            "<b>Subscribed-federation chats</b>: <code>3</code>",
            "<b>Federation chats</b>: <code>0</code> out of <code>4</code>\n"
            "<b>Subscribed federations</b>: <code>2</code>\n"
            "<b>Subscribed-federation chats</b>: <code>3</code>",
            id="subscribed-plural",
        ),
        pytest.param(
            1,
            1,
            1,
            4,
            "<b>Federation chats</b>: <code>1</code>\n"
            "<b>Subscribed federations</b>: <code>1</code>\n"
            "<b>Subscribed-federation chats</b>: <code>1</code>",
            "<b>Federation chats</b>: <code>1</code> out of <code>4</code>\n"
            "<b>Subscribed federations</b>: <code>1</code>\n"
            "<b>Subscribed-federation chats</b>: <code>1</code>",
            id="mixed-singular",
        ),
        pytest.param(
            2,
            2,
            3,
            4,
            "<b>Federation chats</b>: <code>2</code>\n"
            "<b>Subscribed federations</b>: <code>2</code>\n"
            "<b>Subscribed-federation chats</b>: <code>3</code>",
            "<b>Federation chats</b>: <code>2</code> out of <code>4</code>\n"
            "<b>Subscribed federations</b>: <code>2</code>\n"
            "<b>Subscribed-federation chats</b>: <code>3</code>",
            id="mixed-plural",
        ),
    ],
)
def test_ban_reply_and_log_have_exact_harmonized_presentation(
    direct_count: int,
    federation_count: int,
    subscriber_chat_count: int,
    total_chats: int,
    reply_counts: str,
    log_counts: str,
) -> None:
    federation = MagicMock(fed_name="Fed <&>", fed_id="fed<&>")
    user = MagicMock(tid=123, first_name_or_title="User <&>")
    reply = build_ban_reply_doc(
        federation,
        user,
        456,
        "Admin <&>",
        "Reason <&>",
        False,
        banned_count=direct_count,
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
    assert reply.splitlines()[0] == log.splitlines()[0] == title
    assert reply == (
        title + "\n"
        "<b>Federation</b>: Fed &lt;&amp;&gt;\n"
        '<b>User</b>: <a href="tg://user?id=123">User &lt;&amp;&gt;</a>\n'
        '<b>Banned by</b>: <a href="tg://user?id=456">Admin &lt;&amp;&gt;</a>\n'
        "<b>Reason</b>: <code>Reason &lt;&amp;&gt;</code>\n" + reply_counts
    )
    assert log == (
        title + "\n"
        "<b>Federation</b>: Fed &lt;&amp;&gt; (fed&lt;&amp;&gt;)\n"
        '<b>User</b>: <a href="tg://user?id=123">User &lt;&amp;&gt;</a> (<code>123</code>)\n'
        '<b>Banned by</b>: <a href="tg://user?id=456">Admin &lt;&amp;&gt;</a>\n'
        "<b>Reason</b>: <code>Reason &lt;&amp;&gt;</code>\n" + log_counts + "\n"
        "#FedBan\n"
        "<u>Original message</u>:\n"
        "  <tg-spoiler>Original &lt;&amp;&gt;</tg-spoiler>"
    )
    for rendered in (reply, log):
        assert "Result" not in rendered
        assert "Also banned in" not in rendered
        assert "Banned in" not in rendered
        assert rendered.count("<b>Federation chats</b>:") == 1
        assert rendered.count("<b>Subscribed federations</b>:") == bool(federation_count)
        assert rendered.count("<b>Subscribed-federation chats</b>:") == bool(federation_count)
        for unsafe in ("Fed <&>", "fed<&>", "User <&>", "Admin <&>", "Reason <&>", "Original <&>"):
            assert unsafe not in rendered
    for audit_detail in ("#FedBan", "fed&lt;&amp;&gt;", "<code>123</code>", "Original message", "out of"):
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


def test_final_reply_defaults_and_optional_log_details() -> None:
    federation = MagicMock(fed_name="Fed", fed_id="fed")
    user = MagicMock(tid=123, first_name_or_title=None)
    reply = build_ban_reply_doc(federation, user, 456, "Admin", None, False, banner_anonymous=True).to_html()
    log = build_ban_log_doc(federation, user, 456, "Admin", 0, 1, None, None).to_html()
    assert reply == (
        "<b>[🏛 User Banned from Federation]</b>\n"
        "<b>Federation</b>: Fed\n"
        '<b>User</b>: <a href="tg://user?id=123">Unknown</a>\n'
        "<b>Banned by</b>: Anonymous admin\n"
        "<b>Federation chats</b>: <code>0</code>"
    )
    assert log == (
        "<b>[🏛 User Banned from Federation]</b>\n"
        "<b>Federation</b>: Fed (fed)\n"
        '<b>User</b>: <a href="tg://user?id=123">Unknown</a> (<code>123</code>)\n'
        '<b>Banned by</b>: <a href="tg://user?id=456">Admin</a>\n'
        "<b>Federation chats</b>: <code>0</code> out of <code>1</code>\n"
        "#FedBan"
    )
