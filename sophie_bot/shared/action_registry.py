from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from sophie_bot.shared.actions import ActionDefinition, ActionValidationError


def normalize_action_data(
    actions: Mapping[str, ActionDefinition[Any]],
    name: str,
    raw: dict[str, Any],
    *,
    capability: Literal["filter", "flood", "warn"] | None = None,
) -> dict[str, Any]:
    definition = actions.get(name)
    if definition is None:
        raise ActionValidationError(name, "unknown", f"Invalid action name: {name}")

    supported = {
        "filter": definition.as_filter,
        "flood": definition.as_flood,
        "warn": definition.allow_warns,
    }
    if capability is not None and not supported[capability]:
        raise ActionValidationError(
            name,
            "capability",
            f"Action '{name}' cannot be used as a {capability} action",
        )

    if definition.data_object is None:
        return raw
    if not raw and definition.default_data is not None:
        return definition.default_data.model_dump(mode="json")
    try:
        return definition.data_object.model_validate(raw).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as error:
        raise ActionValidationError(
            name,
            "data",
            f"Invalid action data for '{name}': {error}",
        ) from error


def resolve_action_duration(
    actions: Mapping[str, ActionDefinition[Any]],
    name: str,
    raw: dict[str, Any] | None,
) -> timedelta | None:
    definition = actions.get(name)
    if definition is None or definition.duration_field is None:
        return None
    loaded = definition.load_data(raw)
    if not isinstance(loaded, BaseModel):
        return None
    duration = getattr(loaded, definition.duration_field, None)
    return duration if isinstance(duration, timedelta) else None
