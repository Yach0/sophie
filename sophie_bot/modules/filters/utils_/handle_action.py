from dataclasses import dataclass
from typing import Any, Optional

from aiogram.types import Message
from stfu_tg.doc import Element

from sophie_bot.db.models import FiltersModel
from sophie_bot.modules.filters.types.modern_action_abc import ModernActionABC
from sophie_bot.modules.filters.types.modern_action_data_types import ACTION_DATA_DUMPED
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.filters.utils_.legacy_filter_actions import (
    LEGACY_FILTERS_ACTIONS,
)
from sophie_bot.middlewares.connections import ConnectionsMiddleware
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.logger import log


@dataclass(frozen=True)
class EffectiveFilterAction:
    name: str
    data: ACTION_DATA_DUMPED = None
    uses_compatibility_handler: bool = False


def get_effective_filter_actions(filter_item: FiltersModel) -> list[EffectiveFilterAction]:
    if filter_item.actions:
        return [
            EffectiveFilterAction(name=action_name, data=action_data)
            for action_name, action_data in filter_item.actions.items()
        ]

    if filter_item.action:
        return [EffectiveFilterAction(name=filter_item.action, uses_compatibility_handler=True)]

    return []


async def _handle_compatibility_filter_action(message: Message, action_name: str, matched_filter: FiltersModel) -> None:
    if not (action := LEGACY_FILTERS_ACTIONS.get(action_name)):
        raise SophieException("The filter action is not supported!")

    log.debug("handle_compatibility_filter_action", matched_filter=matched_filter)

    connected_chat = await ConnectionsMiddleware.get_current_chat_info(message.chat)
    await action["handle"](message, connected_chat.db_model, matched_filter.model_dump())


async def _handle_modern_filter_action(
    message: Message, action_name: str, data: dict[str, Any], filter_data: ACTION_DATA_DUMPED
) -> Optional[Element | str | LazyProxy]:
    action_item: ModernActionABC = ALL_MODERN_ACTIONS[action_name]

    if filter_data:
        filter_data = action_item.data_object(**filter_data)

    return await action_item.handle(message, data, filter_data)


async def handle_effective_filter_action(
    message: Message, action: EffectiveFilterAction, data: dict[str, Any], matched_filter: FiltersModel
) -> Optional[Element | str | LazyProxy]:
    if action.uses_compatibility_handler:
        await _handle_compatibility_filter_action(message, action.name, matched_filter)
        return None

    return await _handle_modern_filter_action(message, action.name, data, action.data)
