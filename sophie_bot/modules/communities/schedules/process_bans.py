from __future__ import annotations

from datetime import UTC, datetime

from beanie.odm.operators.find.comparison import In

from sophie_bot.db.models import ChatModel, CommunityBanModel, CommunityModel
from sophie_bot.db.models.communities import CommunityTask
from sophie_bot.db.models.communities_enums import CommunityTaskType
from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.modules.communities.services import CommunityBanService
from sophie_bot.modules.communities.utils.ban_docs import (
    build_ban_reply_doc,
    build_ban_superseded_doc,
    build_task_failed_doc,
    build_unban_reply_doc,
)
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.logger import log


class ProcessCommunityBans:
    """Scheduler job that propagates deferred community (un)ban tasks.

    The handler already applied the ban to the DB record and the current chat; this job
    propagates it across the rest of the community's chats and edits the original reply.
    """

    async def handle(self) -> None:
        tasks = await CommunityTask.find(
            In(CommunityTask.task_type, [CommunityTaskType.BAN, CommunityTaskType.UNBAN]),
            CommunityTask.status == TaskStatus.PENDING,
        ).to_list()

        for task in tasks:
            try:
                await self._process_task(task)
            except Exception as exc:  # noqa: BLE001 - keep one bad task from blocking the rest
                log.error("Error processing community ban task", task_id=str(task.id), error=str(exc))

    async def _process_task(self, task: CommunityTask) -> None:
        await self._update_status(task, TaskStatus.PROCESSING)

        try:
            community = await CommunityModel.find_one(CommunityModel.community_tid == task.community_tid)
            if not community:
                raise ValueError(f"Community {task.community_tid} not found")

            if task.task_type == CommunityTaskType.BAN:
                await self._process_ban(task, community)
            else:
                await self._process_unban(task, community)

            await self._update_status(task, TaskStatus.COMPLETED)
        except Exception as exc:
            await self._update_status(task, TaskStatus.FAILED, error_message=str(exc))
            await self._edit_reply(task, build_task_failed_doc(str(exc)).to_html())
            raise

    async def _process_ban(self, task: CommunityTask, community: CommunityModel) -> None:
        if task.target_user_id is None:
            raise ValueError("Ban task is missing the target user ID")

        by_user = await task.user.fetch()
        banner_name = by_user.first_name_or_title if by_user else ""

        ban = await CommunityBanModel.get(task.ban_id) if task.ban_id else None
        if not ban:
            log.warning("Community ban record missing, skipping propagation", task_id=str(task.id))
            await self._edit_reply(task, build_ban_superseded_doc().to_html())
            return

        banned_count = await CommunityBanService.ban_user_in_community_chats(
            community.community_tid,
            ban,
            task.target_user_id,
            current_chat_iid=task.current_chat_iid,
        )
        task.banned_count = banned_count

        user = await ChatModel.get_by_tid(task.target_user_id)
        if user and by_user:
            reply_doc = build_ban_reply_doc(
                community,
                user,
                by_user.tid,
                banner_name,
                task.reason,
                task.silent,
                banned_count=banned_count,
            )
            await self._edit_reply(task, reply_doc.to_html())

    async def _process_unban(self, task: CommunityTask, community: CommunityModel) -> None:
        if task.target_user_id is None:
            raise ValueError("Unban task is missing the target user ID")

        unbanned_count = (
            await CommunityBanService.unban_user_in_chat_iids(list(task.unban_chat_iids), task.target_user_id)
            if task.unban_chat_iids
            else 0
        )
        task.unbanned_count = unbanned_count

        by_user = await task.user.fetch()
        user = await ChatModel.get_by_tid(task.target_user_id)
        if user and by_user:
            reply_doc = build_unban_reply_doc(
                community,
                user,
                by_user.tid,
                by_user.first_name_or_title,
                unbanned_count=unbanned_count,
            )
            await self._edit_reply(task, reply_doc.to_html())

    @staticmethod
    async def _edit_reply(task: CommunityTask, text: str) -> None:
        if not task.reply_chat_id or not task.reply_message_id:
            return
        await common_try(bot.edit_message_text(text, chat_id=task.reply_chat_id, message_id=task.reply_message_id))

    @staticmethod
    async def _update_status(
        task: CommunityTask,
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
