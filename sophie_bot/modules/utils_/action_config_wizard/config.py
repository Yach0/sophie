from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery

from sophie_bot.modules.filters.types.modern_action_abc import ModernActionABC
from sophie_bot.utils.i18n import LazyProxy


@dataclass(frozen=True)
class ActionWizardConfig:
    """Declarative configuration for an Action Config Wizard instance.

    Consumers create one config per wizard and pass it to ``create_action_config_system``
    to get back the handler classes needed for ``__handlers__``.
    """

    # Identity
    module_name: str
    callback_prefix: str
    wizard_title: str | LazyProxy
    success_message: str | LazyProxy

    # Data-access callbacks
    get_model_func: Callable[..., Awaitable[Any]]
    get_actions_func: Callable[..., Awaitable[Any]]
    add_action_func: Callable[..., Awaitable[Any]]
    remove_action_func: Callable[..., Awaitable[Any]]

    # Filters for the command handler
    command_filter: CallbackType
    admin_filter: CallbackType

    # Options
    allow_multiple_actions: bool = True
    default_action_name: Optional[str] = None
    action_filter: Optional[Callable[[ModernActionABC], bool]] = None
    on_back_render: Optional[Callable[[Any, CallbackQuery], Awaitable[None]]] = field(default=None)
