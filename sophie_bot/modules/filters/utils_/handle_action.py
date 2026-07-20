from dataclasses import dataclass
from typing import Any, Optional

from aiogram.types import Message

from sophie_bot.db.models import FiltersModel
from sophie_bot.modules.filters.types.modern_action_abc import ActionResult, ModernActionABC
from sophie_bot.modules.filters.types.modern_action_data_types import ACTION_DATA_DUMPED
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS


@dataclass(frozen=True)
class EffectiveFilterAction:
    name: str
    data: ACTION_DATA_DUMPED = None


def get_effective_filter_actions(filter_item: FiltersModel) -> list[EffectiveFilterAction]:
    if filter_item.actions:
        return [
            EffectiveFilterAction(name=action_name, data=action_data)
            for action_name, action_data in filter_item.actions.items()
        ]

    return []


async def _handle_modern_filter_action(
    message: Message, action_name: str, data: dict[str, Any], filter_data: ACTION_DATA_DUMPED
) -> Optional[ActionResult]:
    action_item: ModernActionABC = ALL_MODERN_ACTIONS[action_name]

    if filter_data and action_item.data_object:
        filter_data = action_item.data_object(**filter_data)

    return await action_item.execute(message, data, filter_data)


async def handle_effective_filter_action(
    message: Message, action: EffectiveFilterAction, data: dict[str, Any], matched_filter: FiltersModel
) -> Optional[ActionResult]:
    _ = matched_filter
    return await _handle_modern_filter_action(message, action.name, data, action.data)
