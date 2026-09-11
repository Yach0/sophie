"""Action definitions and execution adapters shared across Sophie modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Literal, TypeAlias

from aiogram.types import Message
from pydantic import BaseModel, ValidationError
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import LazyProxy


class RestrictionAction(StrEnum):
    BAN = "ban_user"
    KICK = "kick_user"
    MUTE = "mute_user"
    UNBAN = "unban_user"
    UNMUTE = "unmute_user"
    RESTRICT = "restrict_user"


@dataclass(frozen=True, slots=True)
class RestrictionResult:
    action: RestrictionAction
    applied: bool


class StoredAction(BaseModel):
    """Persisted action name and JSON data shared by action-owning models."""

    name: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ActionDefinition[ACTION_DATA: BaseModel | None]:
    """Transport-neutral metadata and persisted-data contract for an action."""

    name: str
    icon: str
    title: LazyProxy | str
    data_object: type[BaseModel] | None = None
    default_data: ACTION_DATA | None = None
    as_filter: bool = True
    as_button: bool = False
    as_flood: bool = False
    allow_warns: bool = True
    skip_for_admins: bool = False
    button_allowed_prefixes: tuple[str, ...] | None = None
    has_interactive_setup: bool = False
    restriction_action: RestrictionAction | None = None
    duration_field: str | None = None

    def load_data(self, data: dict[str, Any] | BaseModel | None) -> BaseModel | None:
        """Load stored data permissively, falling back to the configured default."""
        if data is None or data == {}:
            return self.default_data
        if self.data_object is not None and isinstance(data, self.data_object):
            return data
        if not isinstance(data, dict) or self.data_object is None:
            return self.default_data
        try:
            return self.data_object.model_validate(data)
        except (ValidationError, TypeError, ValueError):
            return self.default_data


class ActionValidationError(ValueError):
    def __init__(
        self,
        name: str,
        reason: Literal["unknown", "capability", "data"],
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.name = name
        self.reason = reason
        self.detail = detail


# What an action may hand back to its dispatcher.
ActionResult: TypeAlias = Element | str | LazyProxy | Message | list[Message]  # noqa: UP040


class ModernActionABC[ACTION_DATA: BaseModel | None](ABC):
    """Bot execution and presentation adapter for an action definition."""

    definition: ClassVar[ActionDefinition[Any]]

    async def execute(
        self,
        message: Message,
        data: dict[str, Any],
        filter_data: ACTION_DATA,
    ) -> ActionResult | None:
        return await self.handle(message, data, filter_data)

    @staticmethod
    @abstractmethod
    def description(data: ACTION_DATA) -> Element | str:
        raise NotImplementedError

    @abstractmethod
    async def handle(
        self,
        message: Message,
        data: dict[str, Any],
        filter_data: ACTION_DATA,
    ) -> ActionResult | None:
        """Handle the action and return its result."""
        raise NotImplementedError
