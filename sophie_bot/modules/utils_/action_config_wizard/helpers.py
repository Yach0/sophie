from __future__ import annotations

from typing import Any

from pydantic import ValidationError


def convert_action_data_to_model(action: Any, action_data: Any) -> Any:
    """Convert stored action data into the matching Pydantic model."""
    if action_data is None or hasattr(action_data, "model_dump"):
        return action.default_data if action_data is None else action_data

    if not isinstance(action_data, dict) or not action_data:
        return action.default_data

    try:
        return action.data_object(**action_data)
    except (ValidationError, TypeError, ValueError):
        return action.default_data
