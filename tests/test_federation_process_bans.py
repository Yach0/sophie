"""Unit tests for the deferred federation task scheduler.

These cover the invariant that matters most operationally: a deferred federation task must
never leave the user's message hanging on "Propagating across the federation…". Every path
out of the scheduler either edits the reply with a result or reports the failure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, User

from sophie_bot.constants import FEDERATION_EXPORT_TTL_DAYS, FEDERATION_TASK_STALE_AFTER_MINUTES
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation, FederationBan, FederationTask
from sophie_bot.db.models.federations_enums import FederationTaskType, TaskStatus
from sophie_bot.modules.federations.schedules.cleanup_tasks import CleanupOldTasks
from sophie_bot.modules.federations.schedules.process_bans import ProcessFederationBans
from sophie_bot.modules.federations.utils.task_failure import build_task_failed_doc
from sophie_bot.modules.utils_.telegram_exceptions import MSG_TO_EDIT_NOT_FOUND

BANNER_TID = 900_001
TARGET_TID = 900_002
REPLY_CHAT_TID = -100_900_003
REPLY_MESSAGE_ID = 4242


async def _make_user(tid: int, name: str) -> ChatModel:
    """Create a user the way production does, then read it back.

    ChatModel declares both `id` and `iid` against the `_id` alias, so a freshly built
    instance carries an `iid` that diverges from the `_id` it is actually stored under.
    Only a model read back from Mongo has `iid == _id`, which is what Link refs resolve
    against - build one by hand and every `Link` to it silently fails to fetch.
    """
    await ChatModel.upsert_user(User(id=tid, is_bot=False, first_name=name))
    user = await ChatModel.get_by_tid(tid)
    assert user is not None
    return user


async def _make_group(tid: int, title: str) -> ChatModel:
    await ChatModel.upsert_group(Chat(id=tid, type="supergroup", title=title))
    group = await ChatModel.get_by_tid(tid)
    assert group is not None
    return group


async def _make_ban_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    banned_count: int = 2,
    propagation_error: Exception | None = None,
) -> tuple[FederationTask, AsyncMock]:
    """Build a ready-to-run BAN task with the federation side-effects mocked out."""
    banner = await _make_user(BANNER_TID, "yachu")
    await _make_user(TARGET_TID, "nikolia")
    chat = await _make_group(REPLY_CHAT_TID, "Test Group")

    federation = Federation(fed_name="OrangeFoxFed", fed_id="fed-1", creator=banner.iid, chats=[chat])
    await federation.insert()

    ban = FederationBan(fed_id="fed-1", user_id=TARGET_TID, time=datetime.now(UTC), by=banner.iid)
    await ban.insert()

    ban_in_chats = (
        AsyncMock(side_effect=propagation_error) if propagation_error else AsyncMock(return_value=banned_count)
    )
    monkeypatch.setattr(
        "sophie_bot.modules.federations.schedules.process_bans.FederationBanService.ban_user_in_federation_chats",
        ban_in_chats,
    )
    monkeypatch.setattr(
        "sophie_bot.modules.federations.schedules.process_bans.FederationBanService."
        "lazy_ban_in_subscribing_federations",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.federations.schedules.process_bans.FederationManageService.post_federation_log",
        AsyncMock(),
    )

    edit_message = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.federations.schedules.process_bans.bot.edit_message_text", edit_message)
    monkeypatch.setattr("sophie_bot.modules.federations.utils.task_failure.bot.edit_message_text", edit_message)

    task = FederationTask(
        fed_id="fed-1",
        task_type=FederationTaskType.BAN,
        target_user_id=TARGET_TID,
        user=banner.iid,
        chat=chat.iid,
        current_chat_iid=chat.iid,
        reply_chat_id=REPLY_CHAT_TID,
        reply_message_id=REPLY_MESSAGE_ID,
        reason="cryptoscam",
        ban_id=ban.id,
        created_at=datetime.now(UTC),
    )
    await task.insert()
    return task, edit_message


def _edited_text(edit_message: AsyncMock) -> str:
    assert edit_message.await_count == 1, "the queued reply must be edited exactly once"
    return edit_message.await_args.args[0]


@pytest.mark.asyncio
async def test_ban_task_edits_reply_with_banner_name(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ChatModel exposes first_name_or_title, not first_name.

    Reading `.first_name` raised AttributeError before propagation even started, so every
    ban task failed and the reply never reached a result.
    """
    task, edit_message = await _make_ban_task(monkeypatch, banned_count=2)

    await ProcessFederationBans().handle()

    text = _edited_text(edit_message)
    assert "yachu" in text, "the banner's display name must survive into the final reply"
    assert "nikolia" in text
    assert "Propagating" not in text, "the reply must leave the in-progress state"

    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.COMPLETED
    assert reloaded.banned_count == 2


@pytest.mark.asyncio
async def test_silent_ban_deletes_reply_only_after_final_edit(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The in-progress reply must survive until propagation edits it with the result."""
    task, edit_message = await _make_ban_task(monkeypatch, banned_count=0)
    task.silent = True
    await task.save()

    schedule_deletion = Mock()
    monkeypatch.setattr(
        "sophie_bot.modules.federations.schedules.process_bans.schedule_message_deletion",
        schedule_deletion,
    )

    await ProcessFederationBans().handle()

    assert "Propagating" not in _edited_text(edit_message)
    schedule_deletion.assert_called_once_with(REPLY_CHAT_TID, [REPLY_MESSAGE_ID])


@pytest.mark.asyncio
async def test_deleted_progress_reply_is_resent(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    task, edit_message = await _make_ban_task(monkeypatch)
    edit_message.side_effect = TelegramBadRequest(method=None, message=MSG_TO_EDIT_NOT_FOUND)  # type: ignore[arg-type]
    send_message = AsyncMock(return_value=Mock(message_id=4343))
    monkeypatch.setattr("sophie_bot.modules.federations.schedules.process_bans.bot.send_message", send_message)

    await ProcessFederationBans().handle()

    send_message.assert_awaited_once()
    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.COMPLETED
    assert reloaded.reply_message_id == 4343


@pytest.mark.asyncio
async def test_anonymous_banner_is_hidden_in_reply_but_kept_in_log(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An anonymous admin's identity is hidden in the public reply but preserved in the fed log.

    The scheduler rebuilds and edits the public reply, so the anonymisation must survive that
    edit; the fed-channel log must still name the real admin for accountability.
    """
    task, edit_message = await _make_ban_task(monkeypatch, banned_count=2)
    task.banner_anonymous = True
    await task.save()

    post_log = AsyncMock()
    monkeypatch.setattr(
        "sophie_bot.modules.federations.schedules.process_bans.FederationManageService.post_federation_log",
        post_log,
    )

    await ProcessFederationBans().handle()

    reply_text = _edited_text(edit_message)
    assert "Anonymous admin" in reply_text, "the public reply must anonymise the banner"
    assert "yachu" not in reply_text, "the real admin name must not leak into the public reply"

    assert post_log.await_count == 1, "the fed log must still be posted"
    log_text = post_log.await_args.args[1]
    assert "yachu" in log_text, "the fed log must keep the real banner name for accountability"


@pytest.mark.asyncio
async def test_ban_task_for_unknown_target_still_reaches_a_result(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target Sophie has never seen (e.g. /fban by raw ID) has no ChatModel.

    Gating the edit on that lookup marked the task COMPLETED while silently leaving the
    reply on "Propagating…" forever - no edit, no log, no error.
    """
    task, edit_message = await _make_ban_task(monkeypatch, banned_count=0)
    unknown_tid = 900_099
    task.target_user_id = unknown_tid
    await task.save()
    assert await ChatModel.get_by_tid(unknown_tid) is None

    await ProcessFederationBans().handle()

    text = _edited_text(edit_message)
    assert "Propagating" not in text, "the reply must reach a result even for an unknown user"

    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_ban_task_with_unresolvable_banner_is_failed_not_silently_completed(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the banner's record is gone, report a failure rather than completing silently."""
    task, edit_message = await _make_ban_task(monkeypatch)
    await ChatModel.find_one(ChatModel.tid == BANNER_TID).delete()

    await ProcessFederationBans().handle()

    text = _edited_text(edit_message)
    assert "❌" in text

    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_orphan_with_no_started_at_is_still_reaped(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PROCESSING task with no started_at must not become immortal.

    Mongo's $lt is type-bracketed and never matches null, so this needs the created_at
    fallback to be reaped at all.
    """
    task, edit_message = await _make_ban_task(monkeypatch)
    task.status = TaskStatus.PROCESSING
    task.started_at = None
    task.created_at = datetime.now(UTC) - timedelta(minutes=FEDERATION_TASK_STALE_AFTER_MINUTES + 1)
    await task.save()

    await CleanupOldTasks().handle()

    assert "❌" in _edited_text(edit_message)
    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_failed_ban_task_reports_failure_and_is_marked_failed(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash mid-propagation must reach the user, not leave the reply on "Propagating…"."""
    task, edit_message = await _make_ban_task(monkeypatch, propagation_error=RuntimeError("telegram exploded"))

    await ProcessFederationBans().handle()

    text = _edited_text(edit_message)
    assert "❌" in text
    assert "telegram exploded" in text, "the underlying error must be surfaced"

    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.FAILED
    assert reloaded.error_message == "telegram exploded"


@pytest.mark.asyncio
async def test_handle_does_not_pick_up_failed_tasks(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """FAILED is terminal - a failed task must not be retried on every 10s tick."""
    _task, edit_message = await _make_ban_task(monkeypatch, propagation_error=RuntimeError("boom"))

    await ProcessFederationBans().handle()
    assert edit_message.await_count == 1

    await ProcessFederationBans().handle()
    assert edit_message.await_count == 1, "a FAILED task must not be picked up again"


@pytest.mark.asyncio
async def test_orphaned_processing_task_is_failed_and_reported(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A task stranded in PROCESSING by a restarted scheduler must not hang forever."""
    task, edit_message = await _make_ban_task(monkeypatch)
    task.status = TaskStatus.PROCESSING
    task.started_at = datetime.now(UTC) - timedelta(minutes=FEDERATION_TASK_STALE_AFTER_MINUTES + 1)
    await task.save()

    await CleanupOldTasks().handle()

    text = _edited_text(edit_message)
    assert "❌" in text

    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.FAILED
    assert reloaded.completed_at is not None


@pytest.mark.asyncio
async def test_recently_started_processing_task_is_left_alone(db_init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A task that is merely slow must never be mistaken for an orphan."""
    task, edit_message = await _make_ban_task(monkeypatch)
    task.status = TaskStatus.PROCESSING
    task.started_at = datetime.now(UTC) - timedelta(minutes=1)
    await task.save()

    await CleanupOldTasks().handle()

    edit_message.assert_not_awaited()
    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.PROCESSING


@pytest.mark.asyncio
async def test_cleanup_expires_completed_but_keeps_failed_forever(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAILED tasks are the record of work still needing a re-do, so they have no TTL."""
    task, _edit_message = await _make_ban_task(monkeypatch)
    long_ago = datetime.now(UTC) - timedelta(days=FEDERATION_EXPORT_TTL_DAYS + 1)

    task.status = TaskStatus.COMPLETED
    task.completed_at = long_ago
    await task.save()

    failed_task = FederationTask(
        fed_id="fed-1",
        task_type=FederationTaskType.BAN,
        target_user_id=TARGET_TID,
        user=task.user,
        chat=task.chat,
        status=TaskStatus.FAILED,
        created_at=long_ago,
        completed_at=long_ago,
    )
    await failed_task.insert()

    await CleanupOldTasks().handle()

    assert await FederationTask.get(task.id) is None, "old COMPLETED tasks should be cleaned up"
    assert await FederationTask.get(failed_task.id) is not None, "old FAILED tasks must be kept indefinitely"


def test_build_task_failed_doc_includes_error() -> None:
    text = build_task_failed_doc("disk on fire").to_html()

    assert "❌" in text
    assert "retry later" in text
    assert "disk on fire" in text


def test_build_task_failed_doc_without_error() -> None:
    assert "retry later" in build_task_failed_doc().to_html()


@pytest.mark.asyncio
async def test_reply_edit_flood_control_does_not_fail_ban_task(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiogram.exceptions import TelegramRetryAfter

    task, edit_message = await _make_ban_task(monkeypatch, banned_count=2)
    edit_message.side_effect = TelegramRetryAfter(
        method=None, message="Too Many Requests: retry after 36", retry_after=36  # type: ignore[arg-type]
    )

    await ProcessFederationBans().handle()

    reloaded = await FederationTask.get(task.id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.COMPLETED
    assert reloaded.banned_count == 2
