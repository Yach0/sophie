from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, Self

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery
from stfu_tg import Button

from sophie_bot.utils.i18n import LazyProxy

if TYPE_CHECKING:
    from sophie_bot.modules.filters.types.modern_action_abc import ModernActionABC


@dataclass
class ActionWizardDraft:
    """JSON-safe aggregate edited by one Action Config Wizard session."""

    actions: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {"actions": self.actions, "metadata": self.metadata}

    @classmethod
    def from_data(cls, data: dict[str, Any] | None) -> Self:
        if not data:
            return cls()
        actions = data.get("actions", {})
        metadata = data.get("metadata", {})
        if not isinstance(actions, dict) or not isinstance(metadata, dict):
            raise TypeError("Invalid action wizard draft")
        return cls(actions=dict(actions), metadata=dict(metadata))

    def replace_action(self, name: str, data: dict[str, Any] | None) -> None:
        self.actions[name] = data

    def remove_action(self, name: str) -> None:
        self.actions.pop(name, None)


class ActionWizardContext(Protocol):
    """Persistence and context presentation adapter for an action wizard."""

    async def load(self, chat_iid: Any) -> ActionWizardDraft: ...

    async def validate(
        self, chat_iid: Any, draft: ActionWizardDraft, event: Any = None, connection: Any = None
    ) -> None: ...

    async def commit(
        self, chat_iid: Any, draft: ActionWizardDraft, event: Any = None, connection: Any = None
    ) -> None: ...

    def update_control(self, draft: ActionWizardDraft, control_name: str) -> bool: ...
    def render_details(self, draft: ActionWizardDraft) -> list[tuple[str, str]]: ...

    def render_controls(self, draft: ActionWizardDraft, callback_prefix: str) -> list[list[Button]]: ...


@dataclass(frozen=True)
class ActionWizardConfig:
    module_name: str
    callback_prefix: str
    wizard_title: str | LazyProxy
    success_message: str | LazyProxy
    context: ActionWizardContext
    command_filter: CallbackType
    admin_filter: CallbackType

    extra_filters: tuple[CallbackType, ...] = ()
    allow_multiple_actions: bool = True
    maximum_actions: int | None = None
    default_action_name: str | None = None
    action_filter: Callable[[ModernActionABC], bool] | None = None
    on_back_render: Callable[[Any, CallbackQuery], Awaitable[None]] | None = field(default=None)
