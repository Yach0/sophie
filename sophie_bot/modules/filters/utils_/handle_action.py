from dataclasses import dataclass
from typing import Any

from aiogram.types import Message
from redis.exceptions import RedisError

from sophie_bot.db.models import FiltersModel
from sophie_bot.modules.filters.utils_.stats import record_filter_action
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.shared.actions import ActionCompletion, ActionResult, ModernActionABC
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.group_whitelist import is_user_group_whitelisted
from sophie_bot.utils.group_whitelist_logging import log_group_whitelist_exemption
from sophie_bot.utils.logger import log


@dataclass(frozen=True)
class EffectiveFilterAction:
    name: str
    data: dict[str, Any] | None = None


def get_effective_filter_actions(filter_item: FiltersModel) -> list[EffectiveFilterAction]:
    if filter_item.actions:
        return [
            EffectiveFilterAction(name=action_name, data=action_data)
            for action_name, action_data in filter_item.actions.items()
        ]

    return []


async def handle_effective_filter_action(
    message: Message,
    action: EffectiveFilterAction,
    data: dict[str, Any],
    matched_filter: FiltersModel | None = None,
) -> ActionResult | None:
    services = data["services"]
    action_item: ModernActionABC = services.modules.action_handlers[action.name]
    definition = services.modules.actions[action.name]
    if definition.skip_for_admins and message.from_user:
        user_tid = message.from_user.id
        if await is_user_group_whitelisted(
            message.chat.id,
            user_tid,
            redis=services.redis,
        ):
            await log_group_whitelist_exemption(message.chat.id, user_tid, "automated_actions")
            return None
        if await is_user_admin(message.chat.id, user_tid):
            log.debug(
                "Modern action: the sender is exempt, skipping...",
                action=definition.name,
            )
            return None
    loaded_data = definition.load_data(action.data)
    completion = ActionCompletion()
    action_data = {**data, "action_completion": completion}
    try:
        return await action_item.handle(message, action_data, loaded_data)
    finally:
        # Completion belongs to the external action, even if later bookkeeping raises.
        if matched_filter is not None and completion.completed:
            try:
                stats_enabled = await is_enabled("filter_stats", chat_tid=message.chat.id, redis=services.redis)
            except RedisError as error:
                log.warning("Could not resolve noncritical filter statistics flag", error=str(error))
                stats_enabled = False
            if stats_enabled:
                chat_iid = data["context"].event_chat.iid
                if completion.deferred:
                    data.setdefault("filter_stats_pending", []).append((chat_iid, matched_filter))
                else:
                    await record_filter_action(chat_iid, matched_filter, redis=services.redis)
