from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from babel.dates import format_date, format_time

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryLine
from sophie_bot.modules.ai.json_schemas.chat_summary import AIChatSummaryGroup
from sophie_bot.modules.ai.schedules.generate_chat_summaries import (
    GenerateChatSummaries,
    _build_summary_doc,
    _build_message_url,
    _derive_summary_line,
)
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages


@pytest.mark.asyncio
async def test_get_cached_messages_filters_out_entries_older_than_48h(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    recent_message = MessageType(
        user_id=1,
        message_id=20,
        text="recent",
        created_at=now - timedelta(hours=2),
        username="alice",
    )
    old_message = MessageType(
        user_id=2,
        message_id=10,
        text="old",
        created_at=now - timedelta(hours=50),
        username="bob",
    )
    raw_messages = [old_message.model_dump_json(), recent_message.model_dump_json()]
    zrangebyscore = AsyncMock(return_value=raw_messages)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.cache_messages.aredis",
        SimpleNamespace(zrangebyscore=zrangebyscore),
    )

    messages = await get_cached_messages(123, now=now)

    assert messages == (recent_message,)


def test_derive_summary_line_uses_first_message_and_unique_usernames() -> None:
    earlier = MessageType(
        user_id=1,
        message_id=100,
        text="hello",
        created_at=datetime(2026, 5, 3, 10, 0, tzinfo=timezone.utc),
        username="alice",
    )
    later = MessageType(
        user_id=2,
        message_id=101,
        text="reply",
        created_at=datetime(2026, 5, 3, 10, 5, tzinfo=timezone.utc),
        username="bob",
    )
    duplicate_user = MessageType(
        user_id=1,
        message_id=102,
        text="follow-up",
        created_at=datetime(2026, 5, 3, 10, 7, tzinfo=timezone.utc),
        username="alice",
    )

    line = _derive_summary_line(
        AIChatSummaryGroup(emoji="🛠", title="Moderation discussion", message_ids=[102, 100, 101]),
        {100: earlier, 101: later, 102: duplicate_user},
    )

    assert line == AIChatSummaryLine(
        emoji="🛠",
        title="Moderation discussion",
        first_message_id=100,
        first_message_at=datetime(2026, 5, 3, 10, 0, tzinfo=timezone.utc),
        usernames=["alice", "bob"],
        source_excerpt="follow-up",
    )


def test_derive_summary_line_skips_low_signal_single_participant_topic() -> None:
    message = MessageType(
        user_id=1,
        message_id=100,
        text="single message",
        created_at=datetime(2026, 5, 3, 10, 0, tzinfo=timezone.utc),
        username="alice",
    )

    line = _derive_summary_line(
        AIChatSummaryGroup(emoji="🧪", title="One-off", message_ids=[100]),
        {100: message},
    )

    assert line is None


@pytest.mark.asyncio
async def test_process_chat_upserts_generated_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    cached_messages = (
        MessageType(
            user_id=1,
            message_id=100,
            text="first",
            created_at=datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc),
            username="alice",
        ),
        MessageType(
            user_id=2,
            message_id=101,
            text="second",
            created_at=datetime(2026, 5, 3, 8, 5, tzinfo=timezone.utc),
            username="bob",
        ),
        MessageType(
            user_id=1,
            message_id=102,
            text="third",
            created_at=datetime(2026, 5, 3, 8, 6, tzinfo=timezone.utc),
            username="alice",
        ),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
        AsyncMock(return_value=cached_messages),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.get_for_date",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        GenerateChatSummaries,
        "generate_summary_groups",
        AsyncMock(
            return_value=SimpleNamespace(
                overview="General overview",
                lines=[AIChatSummaryGroup(emoji="💡", title="Topic", message_ids=[100, 101])],
            )
        ),
    )
    upsert_for_date = AsyncMock()
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.upsert_for_date",
        upsert_for_date,
    )
    send_summary = AsyncMock()
    monkeypatch.setattr(GenerateChatSummaries, "send_summary", send_summary)

    await GenerateChatSummaries().process_chat(chat, summary_date)

    upsert_for_date.assert_awaited_once_with(
        chat,
        summary_date,
        "General overview",
        [
            AIChatSummaryLine(
                emoji="💡",
                title="Topic",
                first_message_id=100,
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc),
                usernames=["alice", "bob"],
                source_excerpt="first",
            )
        ],
    )
    send_summary.assert_awaited_once_with(
        chat.tid,
        summary_date,
        "General overview",
        [
            AIChatSummaryLine(
                emoji="💡",
                title="Topic",
                first_message_id=100,
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc),
                usernames=["alice", "bob"],
                source_excerpt="first",
            )
        ],
    )


@pytest.mark.asyncio
async def test_process_chat_tracks_summary_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    cached_messages = (
        MessageType(
            user_id=1,
            message_id=100,
            text="first",
            created_at=datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc),
            username="alice",
        ),
        MessageType(
            user_id=2,
            message_id=101,
            text="second",
            created_at=datetime(2026, 5, 3, 8, 5, tzinfo=timezone.utc),
            username="bob",
        ),
        MessageType(
            user_id=1,
            message_id=102,
            text="third",
            created_at=datetime(2026, 5, 3, 8, 6, tzinfo=timezone.utc),
            username="alice",
        ),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
        AsyncMock(return_value=cached_messages),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.get_for_date",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        GenerateChatSummaries,
        "generate_summary_groups",
        AsyncMock(
            return_value=SimpleNamespace(
                overview="General overview",
                lines=[AIChatSummaryGroup(emoji="💡", title="Topic", message_ids=[100, 101])],
            )
        ),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.upsert_for_date",
        AsyncMock(),
    )
    monkeypatch.setattr(GenerateChatSummaries, "send_summary", AsyncMock())
    count_metric = Mock()
    monkeypatch.setattr("sophie_bot.modules.ai.schedules.generate_chat_summaries.count_metric", count_metric)

    await GenerateChatSummaries().process_chat(chat, summary_date)

    assert count_metric.call_count == 6
    assert count_metric.call_args_list[0].args == ("sophie.ai.chat_summaries.generated",)
    assert count_metric.call_args_list[0].kwargs == {"attributes": {"summary_kind": "daily"}}
    assert count_metric.call_args_list[1].args == ("sophie.ai.chat_summaries.lines_generated", 1)
    assert count_metric.call_args_list[1].kwargs == {"attributes": {"summary_kind": "daily"}}
    assert count_metric.call_args_list[2].args == ("sophie.ai.chat_summaries.cached_messages", 3)
    assert count_metric.call_args_list[3].args == ("sophie.ai.chat_summaries.grouped_messages", 2)
    assert count_metric.call_args_list[4].args == ("sophie.ai.chat_summaries.coverage_percent", pytest.approx(66.67))
    assert count_metric.call_args_list[5].args == ("sophie.ai.chat_summaries.low_signal_lines_skipped", 0)


@pytest.mark.asyncio
async def test_process_chat_skips_when_summary_for_day_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.get_for_date",
        AsyncMock(return_value=SimpleNamespace()),
    )
    get_cached_messages_between = AsyncMock()
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
        get_cached_messages_between,
    )

    await GenerateChatSummaries().process_chat(chat, summary_date)

    get_cached_messages_between.assert_not_awaited()


def test_build_summary_doc_renders_lines() -> None:
    locale = "en_US"
    doc = _build_summary_doc(
        -1001234567890,
        date(2026, 5, 3),
        "General overview",
        [
            AIChatSummaryLine(
                emoji="💡",
                title="Topic",
                first_message_id=100,
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc),
                usernames=["alice", "bob"],
                source_excerpt="first",
            )
        ],
    )

    html = doc.to_html()
    expected_day = format_date(date(2026, 5, 3), format="long", locale=locale)
    expected_time = format_time(datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc), format="short", locale=locale)

    assert f"Chat history of {expected_day}" in html
    assert "General overview" in html
    assert "Topic" in html
    assert expected_time in html
    assert "💡" in html
    assert "alice" in html
    assert "bob" in html
    assert 'href="https://t.me/c/1234567890/100"' in html


def test_build_summary_doc_renders_lines_with_non_default_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    locale = "de_DE"
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_i18n",
        lambda: SimpleNamespace(current_locale=locale),
    )

    doc = _build_summary_doc(
        -1001234567890,
        date(2026, 5, 3),
        "General overview",
        [
            AIChatSummaryLine(
                emoji="💡",
                title="Topic",
                first_message_id=100,
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc),
                usernames=["alice", "bob"],
                source_excerpt="first",
            )
        ],
    )

    html = doc.to_html()
    expected_day = format_date(date(2026, 5, 3), format="long", locale=locale)
    expected_time = format_time(datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc), format="short", locale=locale)

    assert f"Chat history of {expected_day}" in html
    assert expected_time in html


def test_build_message_url_returns_supergroup_message_url() -> None:
    assert _build_message_url(-1001519075655, 321) == "https://t.me/c/1519075655/321"


def test_build_message_url_returns_none_for_non_supergroup_chat() -> None:
    assert _build_message_url(-123456789, 321) is None
