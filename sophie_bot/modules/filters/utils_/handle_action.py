from typing import Any

from aiogram.types import Message

from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.shared.actions import ActionResult, ModernActionABC
from sophie_bot.utils.group_whitelist import is_user_group_whitelisted
from sophie_bot.utils.group_whitelist_logging import log_group_whitelist_exemption
from sophie_bot.utils.logger import log


async def handle_filter_action(
    message: Message,
    action_name: str,
    action_data: dict[str, Any] | None,
    data: dict[str, Any],
) -> ActionResult | None:
    services = data["services"]
    action_item: ModernActionABC = services.modules.action_handlers[action_name]
    definition = services.modules.actions[action_name]
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
    loaded_data = definition.load_data(action_data)
    return await action_item.handle(message, data, loaded_data)
