from __future__ import annotations

from typing import Any

from pydantic import ValidationError


def convert_action_data_to_model(action: Any, action_data: Any) -> Any:
    """Convert dictionary action data to Pydantic model using action's data_object."""
    if action_data is None:
        return action.default_data

    # If it's already a Pydantic model, return as-is
    if hasattr(action_data, "model_dump"):
        return action_data

    # If it's a dictionary, convert it to the proper Pydantic model
    if isinstance(action_data, dict) and action_data:
        try:
            return action.data_object(**action_data)
        except ValidationError, TypeError, ValueError:
            # If validation fails (e.g., wrong fields), fall back to default data
            # This can happen when action data was stored for a different action type
            return action.default_data

    # Fallback to default data
    return action.default_data
