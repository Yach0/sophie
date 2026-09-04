from __future__ import annotations

from datetime import timedelta
from typing import Any

from sophie_bot.modules.restrictions.actions.base import BaseRestrictionModernAction
from sophie_bot.shared.action_registry import ALL_MODERN_ACTIONS


def resolve_action_duration(action_name: str, action_data: dict[str, Any] | None) -> timedelta | None:
    """Resolve the restriction duration an action was configured with.

    Action data is persisted as ``model_dump(mode="json")`` of the action's own data model,
    so the duration key and its encoding are owned by the action, not by the caller.
    Returns None for actions without a duration and for indefinite restrictions.
    """
    action = ALL_MODERN_ACTIONS.get(action_name)
    if not isinstance(action, BaseRestrictionModernAction):
        return None

    return action.get_duration(action.load_data(action_data))
