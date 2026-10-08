from datetime import UTC, datetime

from aiogram.exceptions import TelegramAPIError

from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.db.models.greetings import (
    WELCOMESECURITY_EXPIRE_DEFAULT_TIME,
    GreetingsModel,
)
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.metrics.welcome import track_captcha_failed
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.group_whitelist import is_user_group_whitelisted
from sophie_bot.utils.group_whitelist_logging import log_group_whitelist_exemption
from sophie_bot.utils.logger import log


async def _is_current_session(ws_user: WSUserModel) -> bool:
    membership = await UserInGroupModel.get_user_in_group(ws_user.user.ref.id, ws_user.group.ref.id)
    if ws_user.membership_id is None and ws_user.membership_join_message_id is None:
        return membership is None or membership.joined_message_id is None
    return (
        membership is not None
        and membership.id == ws_user.membership_id
        and membership.joined_message_id == ws_user.membership_join_message_id
    )


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
        current_ws_user = await WSUserModel.is_user(ws_user.user.ref.id, ws_user.group.ref.id)
        if current_ws_user is None or current_ws_user.passed or current_ws_user.transition == "completing":
            return
        await self._process_current_user(current_ws_user, user, group)

    async def _process_current_user(self, ws_user: WSUserModel, user: ChatModel, group: ChatModel) -> None:
        if not ws_user.id:
            log.error("kick_unpassed_users: skipping ws_user due to missing id", ws_user_tid=str(ws_user.id))
            return
        if not await _is_current_session(ws_user):
            return
        if ws_user.transition == "exempting" or (
            ws_user.transition is None
            and await is_user_group_whitelisted(group.tid, user.tid, redis=self.services.redis)
        ):
            await log_group_whitelist_exemption(group.tid, user.tid, "welcome_security_captcha_autokick")
            if ws_user.transition is None:
                claimed = await ws_user.claim_transition("exempting")
                if claimed is None:
                    return
                ws_user = claimed
            if (
                ws_user.transition != "exempting"
                or not await ws_user.transition_is_current()
                or not await _is_current_session(ws_user)
            ):
                return
            result = await execute_restriction(
                self.services.bot,
                RestrictionAction.UNMUTE,
                group.tid,
                user.tid,
                is_current=ws_user.transition_is_current,
            )
            if result.applied:
                log.debug("kick_unpassed_users: removing exempt user from pending captcha", user=user.tid)
                await ws_user.finish_transition()
            return
        if not await is_enabled("welcomecaptcha_autokick", chat_tid=group.tid, redis=self.services.redis):
            log.debug("kick_unpassed_users: skipped because auto-kick feature flag is disabled", group=group.tid)
            return

        log.debug("kick_unpassed_users: processing user", user=user.id, group=group.id)
        added_at = ws_user.added_at or ws_user.id.generation_time
        if added_at.tzinfo is None:
            added_at = added_at.replace(tzinfo=UTC)
        greetings = await GreetingsModel.get_by_chat_iid(group.iid)
        expiry = (
            greetings.welcome_security.expire
            if greetings.welcome_security and greetings.welcome_security.expire
            else WELCOMESECURITY_EXPIRE_DEFAULT_TIME
        )
        if datetime.now(UTC) - added_at <= expiry:
            log.debug("kick_unpassed_users: skipping ws_user, too young", ws_user_tid=str(ws_user.id))
            return
        if not ws_user.added_at:
            log.warning("kick_unpassed_users: skipping ws_user due to missing added_at", ws_user_tid=str(ws_user.id))
            await ws_user.delete()
            return

        if not await _is_current_session(ws_user):
            return

        if ws_user.transition is None:
            claimed = await ws_user.claim_transition("expiring")
            if claimed is None:
                return
            ws_user = claimed
        if ws_user.transition != "expiring":
            return
        if not await ws_user.transition_is_current() or not await _is_current_session(ws_user):
            return

        track_captcha_failed("timeout")
        action_succeeded = False
        if ws_user.is_join_request:
            try:
                await self.services.bot.decline_chat_join_request(chat_id=group.tid, user_id=user.tid)
                action_succeeded = True
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
                is_current=ws_user.transition_is_current,
            )
            action_succeeded = result.applied
            if action_succeeded:
                log.info("kick_unpassed_users: kicked user", user=user.tid, group=group.tid)

        if action_succeeded:
            await ws_user.finish_transition()

    async def handle(self) -> None:
        log.debug("kick_unpassed_users: starting")
        async for ws_user in WSUserModel.find({"passed": False}):  # skipcq: PYL-E1133
            await self.process_user(ws_user)
        log.debug("kick_unpassed_users: finished")
