from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from babel.dates import format_date, format_time

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryLine
from sophie_bot.modules.ai.json_schemas.chat_summary import AIChatSummaryGroup
from sophie_bot.modules.ai.schedules.generate_chat_summaries import (
    GenerateChatSummaries,
    _build_message_url,
    _build_summary_doc,
    _build_summary_prompt,
    _build_summary_window,
    _derive_summary_line,
)
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages
from sophie_bot.modules.ai.utils.summary_transcript import UNKNOWN_TIME, build_summary_transcript


@pytest.mark.asyncio
async def test_get_cached_messages_filters_out_entries_older_than_48h(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
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


@pytest.mark.asyncio
async def test_get_cached_messages_returns_only_last_n_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    cached_messages = [
        MessageType(
            user_id=index,
            message_id=index,
            text=f"message-{index}",
            created_at=now - timedelta(minutes=40 - index),
            username=f"user-{index}",
        )
        for index in range(40)
    ]
    zrangebyscore = AsyncMock(return_value=[message.model_dump_json() for message in cached_messages])
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.cache_messages.aredis",
        SimpleNamespace(zrangebyscore=zrangebyscore),
    )

    messages = await get_cached_messages(123, now=now, limit=35)

    assert tuple(message.message_id for message in messages) == tuple(range(5, 40))


def test_derive_summary_line_uses_first_message_and_unique_usernames() -> None:
    earlier = MessageType(
        user_id=1,
        message_id=100,
        text="hello",
        created_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        username="alice",
    )
    later = MessageType(
        user_id=2,
        message_id=101,
        text="reply",
        created_at=datetime(2026, 5, 3, 10, 5, tzinfo=UTC),
        username="bob",
    )
    duplicate_user = MessageType(
        user_id=1,
        message_id=102,
        text="follow-up",
        created_at=datetime(2026, 5, 3, 10, 7, tzinfo=UTC),
        username="alice",
    )

    line = _derive_summary_line(
        AIChatSummaryGroup(emoji="🛠", title="Moderation discussion", message_refs=[102, 100, 101]),
        {100: earlier, 101: later, 102: duplicate_user},
    )

    assert line == AIChatSummaryLine(
        emoji="🛠",
        title="Moderation discussion",
        first_message_id=100,
        first_message_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        usernames=["alice", "bob"],
        source_excerpt="follow-up",
    )


def test_derive_summary_line_skips_low_signal_single_participant_topic() -> None:
    message = MessageType(
        user_id=1,
        message_id=100,
        text="single message",
        created_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        username="alice",
    )

    line = _derive_summary_line(
        AIChatSummaryGroup(emoji="🧪", title="One-off", message_refs=[100]),
        {100: message},
    )

    assert line is None


def _patch_privacy_flag(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    """Stub the privacy feature flag.

    ``process_chat`` resolves ``ai_summary_improved_privacy`` through Redis and, on a cache miss,
    through the ``FeatureFlagOverride`` document. Unit tests do not initialize Beanie, so the
    lookup must be stubbed instead of relying on whatever state earlier tests left behind.
    """
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.is_enabled",
        AsyncMock(return_value=enabled),
    )


@pytest.mark.asyncio
async def test_process_chat_upserts_generated_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    _patch_privacy_flag(monkeypatch, enabled=False)
    cached_messages = (
        MessageType(
            user_id=1,
            message_id=100,
            text="first",
            created_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
            username="alice",
        ),
        MessageType(
            user_id=2,
            message_id=101,
            text="second",
            created_at=datetime(2026, 5, 3, 8, 5, tzinfo=UTC),
            username="bob",
        ),
        MessageType(
            user_id=1,
            message_id=102,
            text="third",
            created_at=datetime(2026, 5, 3, 8, 6, tzinfo=UTC),
            username="alice",
        ),
    )
    get_cached_messages_between = AsyncMock(return_value=cached_messages)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
        get_cached_messages_between,
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
                lines=[AIChatSummaryGroup(emoji="💡", title="Topic", message_refs=[100, 101])],
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

    current_time = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)

    await GenerateChatSummaries().process_chat(chat, summary_date, now=current_time)

    expected_window_start, expected_window_end = _build_summary_window(current_time)
    get_cached_messages_between.assert_awaited_once_with(chat.tid, expected_window_start, expected_window_end)

    upsert_for_date.assert_awaited_once_with(
        chat,
        summary_date,
        "General overview",
        [
            AIChatSummaryLine(
                emoji="💡",
                title="Topic",
                first_message_id=100,
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
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
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
                usernames=["alice", "bob"],
                source_excerpt="first",
            )
        ],
    )


@pytest.mark.asyncio
async def test_process_chat_tracks_summary_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    _patch_privacy_flag(monkeypatch, enabled=False)
    cached_messages = (
        MessageType(
            user_id=1,
            message_id=100,
            text="first",
            created_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
            username="alice",
        ),
        MessageType(
            user_id=2,
            message_id=101,
            text="second",
            created_at=datetime(2026, 5, 3, 8, 5, tzinfo=UTC),
            username="bob",
        ),
        MessageType(
            user_id=1,
            message_id=102,
            text="third",
            created_at=datetime(2026, 5, 3, 8, 6, tzinfo=UTC),
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
                lines=[AIChatSummaryGroup(emoji="💡", title="Topic", message_refs=[100, 101])],
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

    await GenerateChatSummaries().process_chat(chat, summary_date, now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC))

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
async def test_process_chat_upserts_empty_summary_when_no_lines_generated(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    _patch_privacy_flag(monkeypatch, enabled=False)
    cached_messages = (
        MessageType(
            user_id=1,
            message_id=100,
            text="first",
            created_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
            username="alice",
        ),
        MessageType(
            user_id=2,
            message_id=101,
            text="second",
            created_at=datetime(2026, 5, 3, 8, 5, tzinfo=UTC),
            username="bob",
        ),
        MessageType(
            user_id=1,
            message_id=102,
            text="third",
            created_at=datetime(2026, 5, 3, 8, 6, tzinfo=UTC),
            username="alice",
        ),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.get_for_date",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
        AsyncMock(return_value=cached_messages),
    )
    monkeypatch.setattr(
        GenerateChatSummaries,
        "generate_summary_groups",
        AsyncMock(
            return_value=SimpleNamespace(
                overview="General overview",
                lines=[AIChatSummaryGroup(emoji="💡", title="Topic", message_refs=[100])],
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

    await GenerateChatSummaries().process_chat(chat, summary_date, now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC))

    upsert_for_date.assert_awaited_once_with(chat, summary_date, "General overview", [])
    send_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_chat_skips_when_summary_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    for has_lines in (True, False):
        existing_lines = (
            [
                AIChatSummaryLine(
                    emoji="💡",
                    title="Topic",
                    first_message_id=100,
                    first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
                    usernames=["alice"],
                )
            ]
            if has_lines
            else []
        )
        monkeypatch.setattr(
            "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.get_for_date",
            AsyncMock(return_value=SimpleNamespace(lines=existing_lines)),
        )
        get_cached_messages_between = AsyncMock()
        monkeypatch.setattr(
            "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
            get_cached_messages_between,
        )
        generate_summary_groups = AsyncMock()
        monkeypatch.setattr(GenerateChatSummaries, "generate_summary_groups", generate_summary_groups)

        await GenerateChatSummaries().process_chat(chat, summary_date)

        get_cached_messages_between.assert_not_awaited()
        generate_summary_groups.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_chat_force_bypasses_existing_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-100123)
    summary_date = date(2026, 5, 3)
    _patch_privacy_flag(monkeypatch, enabled=False)

    # An existing summary with lines would normally cause a skip.
    existing_line = AIChatSummaryLine(
        emoji="\U0001f4a1",
        title="Old Topic",
        first_message_id=50,
        first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
        usernames=["alice"],
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.get_for_date",
        AsyncMock(return_value=SimpleNamespace(lines=[existing_line])),
    )
    cached_messages = tuple(
        MessageType(
            user_id=user_id,
            message_id=msg_id,
            text=f"message {msg_id}",
            created_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
            username=f"user{user_id}",
        )
        for user_id, msg_id in enumerate((100, 101, 102), start=1)
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
        AsyncMock(return_value=cached_messages),
    )
    monkeypatch.setattr(
        GenerateChatSummaries,
        "generate_summary_groups",
        AsyncMock(
            return_value=SimpleNamespace(
                overview="Overview",
                lines=[AIChatSummaryGroup(emoji="\U0001f4a1", title="Topic", message_refs=[100, 101, 102])],
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

    await GenerateChatSummaries().process_chat(
        chat, summary_date, force=True, now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    )

    upsert_for_date.assert_awaited_once()
    send_summary.assert_awaited_once()


def test_build_summary_window_returns_current_day_to_now() -> None:
    current_time = datetime(2026, 5, 4, 23, 30, 8, tzinfo=UTC)

    window_start, window_end = _build_summary_window(current_time)

    assert window_end == current_time
    assert window_start == datetime(2026, 5, 4, 0, 0, tzinfo=UTC)


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
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
                usernames=["alice", "bob"],
                source_excerpt="first",
            )
        ],
    )

    html = doc.to_html()
    expected_day = format_date(date(2026, 5, 3), format="long", locale=locale)
    expected_time = format_time(datetime(2026, 5, 3, 8, 0, tzinfo=UTC), format="short", locale=locale)

    assert f"Chat history of {expected_day}" in html
    assert "General overview" in html
    assert "Topic" in html
    assert expected_time in html
    assert "💡" in html
    assert "alice" in html
    assert "bob" in html
    assert 'href="https://t.me/c/1234567890/100"' in html


def test_build_summary_doc_uses_inline_simple_header() -> None:
    doc = _build_summary_doc(-1001234567890, date(2026, 5, 3), "General overview", [], "simple")

    html = doc.to_html()

    assert html.startswith("✨ 🔋 ")
    assert "\nChat history" not in html
    assert "Chat history" in html


def test_build_summary_doc_can_disable_ai_header() -> None:
    doc = _build_summary_doc(-1001234567890, date(2026, 5, 3), "General overview", [], "disable")

    html = doc.to_html()

    assert not html.startswith("✨")
    assert "Chat history" in html


def test_build_summary_doc_orders_lines_by_first_message_time() -> None:
    doc = _build_summary_doc(
        -1001234567890,
        date(2026, 5, 3),
        "General overview",
        [
            AIChatSummaryLine(
                emoji="🌙",
                title="Evening topic",
                first_message_id=200,
                first_message_at=datetime(2026, 5, 3, 20, 0, tzinfo=UTC),
                usernames=["bob"],
                source_excerpt="later",
            ),
            AIChatSummaryLine(
                emoji="☀️",
                title="Morning topic",
                first_message_id=100,
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
                usernames=["alice"],
                source_excerpt="earlier",
            ),
        ],
    )

    html = doc.to_html()

    assert html.index("Morning topic") < html.index("Evening topic")


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
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
                usernames=["alice", "bob"],
                source_excerpt="first",
            )
        ],
    )

    html = doc.to_html()
    expected_day = format_date(date(2026, 5, 3), format="long", locale=locale)
    expected_time = format_time(datetime(2026, 5, 3, 8, 0, tzinfo=UTC), format="short", locale=locale)

    assert f"Chat history of {expected_day}" in html
    assert expected_time in html


def test_build_message_url_returns_supergroup_message_url() -> None:
    assert _build_message_url(-1001519075655, 321) == "https://t.me/c/1519075655/321"


def _transcript_sample() -> tuple[MessageType, ...]:
    return (
        MessageType(
            user_id=777,
            message_id=45201,
            text="hello\nthere",
            created_at=datetime(2026, 5, 3, 8, 0, tzinfo=UTC),
            username="alice",
        ),
        MessageType(
            user_id=888,
            message_id=45202,
            text="hi",
            created_at=datetime(2026, 5, 3, 8, 5, tzinfo=UTC),
            username="bob",
        ),
        MessageType(
            user_id=777,
            message_id=45203,
            text="bye",
            created_at=datetime(2026, 5, 3, 8, 6, tzinfo=UTC),
            username="alice",
        ),
    )


def test_build_summary_transcript_anonymized_uses_positions_and_pseudonyms() -> None:
    transcript = build_summary_transcript(_transcript_sample(), anonymize=True)

    assert transcript.text == (
        "[1] [08:00] [speaker1] hello there\n[2] [08:05] [speaker2] hi\n[3] [08:06] [speaker1] bye"
    )
    assert transcript.messages_by_reference == dict(enumerate(_transcript_sample(), start=1))


def test_build_summary_transcript_anonymized_leaks_no_telegram_identifiers() -> None:
    text = build_summary_transcript(_transcript_sample(), anonymize=True).text

    for identifier in ("45201", "45202", "45203", "777", "888", "alice", "bob", "2026-05-03"):
        assert identifier not in text


def test_build_summary_transcript_plain_keeps_ids_usernames_and_timestamps() -> None:
    transcript = build_summary_transcript(_transcript_sample(), anonymize=False)

    assert transcript.text.splitlines()[0] == "[id=45201] [2026-05-03T08:00:00+00:00] [alice] hello there"
    assert transcript.messages_by_reference == {message.message_id: message for message in _transcript_sample()}


def test_build_summary_transcript_renders_placeholder_for_missing_timestamp() -> None:
    message = MessageType(user_id=1, message_id=5, text="no time", created_at=None, username="alice")

    assert build_summary_transcript((message,), anonymize=True).text == f"[1] [{UNKNOWN_TIME}] [speaker1] no time"


def test_build_summary_prompt_combines_instructions_format_and_transcript() -> None:
    transcript = build_summary_transcript(_transcript_sample(), anonymize=True)

    prompt = _build_summary_prompt(transcript, "Instructions")

    assert prompt == f"Instructions\n{transcript.format_instructions}\n\n{transcript.text}"


def _summary_groups(*message_refs: list[int]) -> SimpleNamespace:
    return SimpleNamespace(
        overview="General overview",
        lines=[AIChatSummaryGroup(emoji="💡", title="Topic", message_refs=refs) for refs in message_refs],
    )


def _patch_process_chat_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    privacy_enabled: bool,
    generate_summary_groups: AsyncMock,
) -> tuple[AsyncMock, AsyncMock]:
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.get_for_date",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.get_cached_messages_between",
        AsyncMock(return_value=_transcript_sample()),
    )
    _patch_privacy_flag(monkeypatch, enabled=privacy_enabled)
    monkeypatch.setattr(GenerateChatSummaries, "generate_summary_groups", generate_summary_groups)
    upsert_for_date = AsyncMock()
    monkeypatch.setattr(
        "sophie_bot.modules.ai.schedules.generate_chat_summaries.AIChatSummaryModel.upsert_for_date",
        upsert_for_date,
    )
    send_summary = AsyncMock()
    monkeypatch.setattr(GenerateChatSummaries, "send_summary", send_summary)
    return upsert_for_date, send_summary


async def _run_process_chat(chat: SimpleNamespace) -> None:
    await GenerateChatSummaries().process_chat(chat, date(2026, 5, 3), now=datetime(2026, 5, 3, 12, 0, tzinfo=UTC))


@pytest.mark.asyncio
async def test_process_chat_sends_anonymized_transcript_and_resolves_real_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-1001234567890)
    generate_summary_groups = AsyncMock(return_value=_summary_groups([1, 2]))
    _, send_summary = _patch_process_chat_dependencies(
        monkeypatch, privacy_enabled=True, generate_summary_groups=generate_summary_groups
    )

    await _run_process_chat(chat)

    sent_transcript = generate_summary_groups.await_args.args[0]
    for identifier in ("45201", "45202", "45203", "777", "888", "alice", "bob"):
        assert identifier not in sent_transcript.text

    # Positional references still resolve back to the real message the deep link points at.
    (line,) = send_summary.await_args.args[3]
    assert line.first_message_id == 45201
    assert line.usernames == ["alice", "bob"]


@pytest.mark.asyncio
async def test_process_chat_retries_once_when_model_invents_references(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-1001234567890)
    generate_summary_groups = AsyncMock(side_effect=[_summary_groups([1, 99]), _summary_groups([1, 2])])
    upsert_for_date, send_summary = _patch_process_chat_dependencies(
        monkeypatch, privacy_enabled=True, generate_summary_groups=generate_summary_groups
    )

    await _run_process_chat(chat)

    assert generate_summary_groups.await_count == 2
    upsert_for_date.assert_awaited_once()
    send_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_chat_discards_summary_when_retry_also_invents_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-1001234567890)
    generate_summary_groups = AsyncMock(side_effect=[_summary_groups([1, 99]), _summary_groups([2, 98])])
    upsert_for_date, send_summary = _patch_process_chat_dependencies(
        monkeypatch, privacy_enabled=True, generate_summary_groups=generate_summary_groups
    )

    await _run_process_chat(chat)

    assert generate_summary_groups.await_count == 2
    upsert_for_date.assert_not_awaited()
    send_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_chat_drops_unknown_references_without_retrying_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = SimpleNamespace(iid="chat-iid", tid=-1001234567890)
    generate_summary_groups = AsyncMock(return_value=_summary_groups([45201, 45202, 999]))
    upsert_for_date, send_summary = _patch_process_chat_dependencies(
        monkeypatch, privacy_enabled=False, generate_summary_groups=generate_summary_groups
    )

    await _run_process_chat(chat)

    assert generate_summary_groups.await_count == 1
    upsert_for_date.assert_awaited_once()
    send_summary.assert_awaited_once()
