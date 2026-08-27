from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from beanie.odm.operators.find.comparison import In

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.federations import Federation, FederationBan, FederationTask
from sophie_bot.db.models.federations_enums import FederationTaskType, TaskStatus
from sophie_bot.modules.federations.services import FederationBanService, FederationManageService
from sophie_bot.modules.federations.utils.ban_docs import (
    build_ban_log_doc,
    build_ban_reply_doc,
    build_ban_superseded_doc,
    build_unban_log_text,
    build_unban_reply_doc,
)
from sophie_bot.modules.federations.utils.task_failure import notify_task_failed
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.delayed_delete import schedule_message_deletion
from sophie_bot.services.bot import bot
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


async def send_replacement(task: FederationTask, chat_id: int, text: str) -> None:
    message = await bot.send_message(chat_id, text)
    task.reply_message_id = message.message_id


async def _edit_or_resend_reply(task: FederationTask, text: str) -> None:
    if task.reply_chat_id and task.reply_message_id:
        try:
            await common_try(
                bot.edit_message_text(text, chat_id=task.reply_chat_id, message_id=task.reply_message_id),
                edit_not_found=partial(send_replacement, task, task.reply_chat_id, text),
            )
        except TelegramRetryAfter as err:
            log.warning(
                "Telegram flood control exceeded while editing federation reply",
                retry_after=err.retry_after,
                task_id=str(task.id),
                chat_id=task.reply_chat_id,
            )
        except TelegramAPIError as err:
            log.warning(
                "Telegram API error while editing federation reply",
                error=str(err),
                task_id=str(task.id),
                chat_id=task.reply_chat_id,
            )


def schedule_silent_reply_deletion(task: FederationTask) -> None:
    if task.silent and task.reply_chat_id and task.reply_message_id:
        schedule_message_deletion(task.reply_chat_id, [task.reply_message_id])


class ProcessFederationBans:
    """Scheduler job that propagates deferred federation (un)ban tasks.

    The handler already applied the ban to the DB record and the current chat; this
    job propagates it across the rest of the federation's chats and the subscriber
    chain, then edits the original reply with the final counts and posts the fed log.
    """

    async def handle(self) -> None:
        """Process all pending federation ban tasks."""
        tasks = await FederationTask.find(
            In(FederationTask.task_type, [FederationTaskType.BAN, FederationTaskType.UNBAN]),
            FederationTask.status == TaskStatus.PENDING,
        ).to_list()

        for task in tasks:
            try:
                await self._process_task(task)
            except Exception as err:  # noqa: BLE001 - keep one bad task from blocking the rest
                log.error("Error processing federation ban task", task_id=str(task.id), exc_info=err)

    async def _process_task(self, task: FederationTask) -> None:
        await self._update_status(task, TaskStatus.PROCESSING)

        try:
            federation = await Federation.find_one(Federation.fed_id == task.fed_id)
            if not federation:
                raise ValueError(f"Federation {task.fed_id} not found")

            if task.task_type == FederationTaskType.BAN:
                await self._process_ban(task, federation)
            else:
                await self._process_unban(task, federation)

            await self._update_status(task, TaskStatus.COMPLETED)
            schedule_silent_reply_deletion(task)
        except Exception as err:
            # Mark FAILED and surface it instead of leaving the reply on "Propagating…".
            # FAILED tasks are kept indefinitely so the cause can be found and the task re-done.
            await self._update_status(task, TaskStatus.FAILED, error_message=str(err))
            await notify_task_failed(task, str(err))
            await task.save()
            schedule_silent_reply_deletion(task)
            raise

    async def _process_ban(self, task: FederationTask, federation: Federation) -> None:
        if task.target_user_id is None:
            raise ValueError("Ban task is missing the target user ID")

        by_user = await self._require_user(task)
        banner_name = by_user.first_name_or_title or _("Unknown")

        ban = await FederationBan.get(task.ban_id) if task.ban_id else None
        if not ban:
            # The ban record is gone (e.g. the user was unbanned before this ran) - nothing to do.
            # Edit the queued reply to a terminal state so it doesn't stay on "Propagating…".
            log.warning("Federation ban record missing, skipping propagation", task_id=str(task.id))
            text = build_ban_superseded_doc().to_html()
            await _edit_or_resend_reply(task, text)
            return

        banned_count = await FederationBanService.ban_user_in_federation_chats(
            federation,
            ban,
            task.target_user_id,
            current_chat_iid=task.current_chat_iid,
        )

        lazy_bans = await FederationBanService.lazy_ban_in_subscribing_federations(
            federation,
            task.target_user_id,
            by_user.iid,
            task.reason,
            task.original_message_text,
        )
        lazy_ban_count = len(lazy_bans)

        task.banned_count = banned_count
        task.lazy_ban_count = lazy_ban_count

        user = await self._resolve_target(task.target_user_id)
        reply_doc = build_ban_reply_doc(
            federation,
            user,
            by_user.tid,
            banner_name,
            task.reason,
            task.silent,
            banned_count=banned_count,
            lazy_ban_count=lazy_ban_count,
            banner_anonymous=task.banner_anonymous,
        )
        text = reply_doc.to_html()
        await _edit_or_resend_reply(task, text)

        total_chats = len(federation.chats) if federation.chats else 0
        log_doc = build_ban_log_doc(
            federation,
            user,
            banner_name,
            banned_count,
            total_chats,
            task.reason,
            task.original_message_text,
        )
        await FederationManageService.post_federation_log(federation, log_doc.to_html(), bot)

    async def _process_unban(self, task: FederationTask, federation: Federation) -> None:
        if task.target_user_id is None:
            raise ValueError("Unban task is missing the target user ID")

        unbanned_count = (
            await FederationBanService.unban_user_in_chat_iids(list(task.unban_chat_iids), task.target_user_id)
            if task.unban_chat_iids
            else 0
        )
        task.unbanned_count = unbanned_count

        by_user = await self._require_user(task)
        unbanner_name = by_user.first_name_or_title or _("Unknown")

        user = await self._resolve_target(task.target_user_id)
        reply_doc = build_unban_reply_doc(
            federation,
            user,
            by_user.tid,
            unbanner_name,
            unbanned_count=unbanned_count,
        )
        text = reply_doc.to_html()
        await _edit_or_resend_reply(task, text)

        log_text = build_unban_log_text(user, by_user.tid, unbanner_name)
        await FederationManageService.post_federation_log(federation, log_text, bot)

    @staticmethod
    async def _require_user(task: FederationTask) -> ChatModel:
        """Resolve the user who issued the task, raising if that record is gone.

        Beanie hands back the ``Link`` itself rather than raising when the referenced
        document no longer exists, and a Link is truthy - so a plain falsy check would let
        it through and fail later on attribute access. The handler refuses to enqueue
        without a saved banner, so an unresolvable one is genuinely exceptional: raising
        routes it to the FAILED path, which still reports a terminal state to the user.
        """
        by_user = await task.user.fetch()
        if not isinstance(by_user, ChatModel):
            raise TypeError("The user who issued the task could not be resolved")
        return by_user

    @staticmethod
    async def _resolve_target(target_user_id: int) -> ChatModel:
        """Resolve the (un)banned user, falling back to an ID-only stand-in.

        A target Sophie has never seen has no ChatModel - e.g. /fban by raw ID. That must
        not stop the reply from reaching a result, so fall back rather than return None:
        gating the edit on this is what left replies stuck on "Propagating…" forever.
        """
        return await ChatModel.get_by_tid(target_user_id) or ChatModel.user_from_id(target_user_id)

    @staticmethod
    async def _update_status(
        task: FederationTask,
        status: TaskStatus,
        error_message: str | None = None,
    ) -> None:
        task.status = status
        if error_message:
            task.error_message = error_message
        if status == TaskStatus.PROCESSING:
            task.started_at = datetime.now(UTC)
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.completed_at = datetime.now(UTC)
        await task.save()
