from dataclasses import dataclass
from typing import Any

from aiogram.types import Message

from sophie_bot.db.models import FiltersModel
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.shared.actions import ActionResult, ModernActionABC
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


async def _handle_modern_filter_action(
    message: Message, action_name: str, data: dict[str, Any], filter_data: dict[str, Any] | None
) -> ActionResult | None:
    services = data["services"]
    action_item: ModernActionABC = services.modules.action_handlers[action_name]
    definition = services.modules.actions[action_name]
    if definition.skip_for_admins and message.from_user and await is_user_admin(message.chat.id, message.from_user.id):
        log.debug(
            "Modern action: the sender is an admin, skipping...",
            action=definition.name,
        )
        return None
    loaded_data = definition.load_data(filter_data)
    return await action_item.execute(message, data, loaded_data)


async def handle_effective_filter_action(
    message: Message, action: EffectiveFilterAction, data: dict[str, Any], matched_filter: FiltersModel
) -> ActionResult | None:
    _ = matched_filter
    return await _handle_modern_filter_action(message, action.name, data, action.data)
