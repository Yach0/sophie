from __future__ import annotations

from typing import Any, Optional

from aiogram.types import CallbackQuery, Message
from beanie import PydanticObjectId
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
        except (ValidationError, TypeError, ValueError):
            # If validation fails (e.g., wrong fields), fall back to default data
            # This can happen when action data was stored for a different action type
            return action.default_data

    # Fallback to default data
    return action.default_data


async def _show_action_configured_message(
    event: CallbackQuery | Message,
    action_name: str,
    chat_tid: PydanticObjectId,
    callback_prefix: str,
    success_message: str,
    action_data: Optional[dict[str, Any]] = None,
    *,
    show_delete: bool = True,
    show_cancel: bool = True,
) -> None:
    """Deprecated: delegates to WizardRenderer for backward compatibility."""
    from sophie_bot.modules.utils_.action_config_wizard.handler import WizardRenderer

    await WizardRenderer.send_action_configured(
        event,
        action_name=action_name,
        callback_prefix=callback_prefix,
        success_message=success_message,
        action_data=action_data,
        show_delete=show_delete,
        show_cancel=show_cancel,
    )
