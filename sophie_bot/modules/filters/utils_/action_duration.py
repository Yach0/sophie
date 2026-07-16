from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.restrictions.actions.base import BaseRestrictionModernAction
from sophie_bot.modules.utils_.action_config_wizard.helpers import convert_action_data_to_model


def resolve_action_duration(action_name: str, action_data: Optional[dict[str, Any]]) -> Optional[timedelta]:
    """Resolve the restriction duration an action was configured with.

    Action data is persisted as ``model_dump(mode="json")`` of the action's own data model,
    so the duration key and its encoding are owned by the action, not by the caller.
    Returns None for actions without a duration and for indefinite restrictions.
    """
    action = ALL_MODERN_ACTIONS.get(action_name)
    if not isinstance(action, BaseRestrictionModernAction):
        return None

    return action.get_duration(convert_action_data_to_model(action, action_data))
