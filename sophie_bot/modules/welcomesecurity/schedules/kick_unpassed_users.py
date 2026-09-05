from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramAPIError

from sophie_bot.constants import WELCOMESECURITY_KICK_TIMEOUT_HOURS
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.metrics.welcome import track_captcha_failed
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted
from sophie_bot.utils.logger import log


class KickUnpassedUsers:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    async def process_user(self, ws_user: WSUserModel) -> None:
        if ws_user.passed:
            log.debug("kick_unpassed_users: skipping ws_user, already passed", ws_user_tid=str(ws_user.id))
            return
        if not ws_user.id:
            log.error("kick_unpassed_users: skipping ws_user due to missing id", ws_user_tid=str(ws_user.id))
            return
        try:
            user = await ChatModel.get_by_iid(ws_user.user.ref.id)
            group = await ChatModel.get_by_iid(ws_user.group.ref.id)
        except AttributeError as error:
            log.warning(
                "kick_unpassed_users: skipping ws_user due to invalid link references",
                ws_user_tid=str(ws_user.id),
                error=str(error),
            )
            await ws_user.delete()
            return
        if user is None or group is None:
            log.warning(
                "kick_unpassed_users: skipping ws_user due to missing linked user/group",
                ws_user_tid=str(ws_user.id),
            )
            await ws_user.delete()
            return
        if await is_user_globally_whitelisted(user.tid):
            log.debug("kick_unpassed_users: removing exempt user from pending captcha", user=user.tid)
            await ws_user.delete()
            return
        if not await is_enabled(
            "welcomecaptcha_autokick",
            chat_tid=group.tid,
            redis=self.services.redis,
        ):
            log.debug("kick_unpassed_users: skipped because auto-kick feature flag is disabled", group=group.tid)
            return

        log.debug("kick_unpassed_users: processing user", user=user.id, group=group.id)
        added_at = ws_user.added_at or ws_user.id.generation_time
        if added_at.tzinfo is None:
            added_at = added_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - added_at <= timedelta(hours=WELCOMESECURITY_KICK_TIMEOUT_HOURS):
            log.debug("kick_unpassed_users: skipping ws_user, too young", ws_user_tid=str(ws_user.id))
            return
        if not ws_user.added_at:
            log.warning("kick_unpassed_users: skipping ws_user due to missing added_at", ws_user_tid=str(ws_user.id))
            await ws_user.delete()
            return

        track_captcha_failed("timeout")
        if ws_user.is_join_request:
            try:
                await self.services.bot.decline_chat_join_request(chat_id=group.tid, user_id=user.tid)
                log.info("kick_unpassed_users: declined join request", user=user.tid, group=group.tid)
            except TelegramAPIError as error:
                log.warning(
                    "kick_unpassed_users: failed to decline join request",
                    user=user.tid,
                    group=group.tid,
                    error=str(error),
                )
        else:
            result = await execute_restriction(
                self.services.bot,
                RestrictionAction.KICK,
                group.tid,
                user.tid,
            )
            if result.applied:
                log.info("kick_unpassed_users: kicked user", user=user.tid, group=group.tid)

        await ws_user.delete()

    async def handle(self) -> None:
        log.debug("kick_unpassed_users: starting")
        async for ws_user in WSUserModel.find({"passed": False}):  # skipcq: PYL-E1133
            await self.process_user(ws_user)
        log.debug("kick_unpassed_users: finished")
