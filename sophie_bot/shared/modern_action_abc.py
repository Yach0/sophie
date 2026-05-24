"""Modern action abstract base classes shared across modules.

Extracted from sophie_bot.modules.filters.types.modern_action_abc to break the
notes ↔ filters circular dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

from aiogram import Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from pydantic import BaseModel
from stfu_tg.doc import Element

from sophie_bot.utils.handlers import SophieMessageCallbackQueryHandler
from sophie_bot.utils.i18n import LazyProxy

ACTION_DATA = TypeVar("ACTION_DATA", bound=BaseModel | None)


class FilterActionSetupHandlerABC(SophieMessageCallbackQueryHandler, ABC):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (lambda _: False,)

    @classmethod
    def register(cls, router: Router) -> None:
        # We don't need to register filter action handlers in the dispatcher,
        # since it's going to be executed from other handlers
        pass


@dataclass
class ActionSetupMessage:
    text: str
    reply_markup: Optional[InlineKeyboardMarkup] = None


class ActionSetupTryAgainException(Exception):
    pass


@dataclass
class ModernActionSetting(Generic[ACTION_DATA]):
    """Setting descriptor for a modern action.

    setup_confirm can return ActionSetupTryAgainException that will not switch
    the current state, so users would have another attempt to setup the filter.
    """

    title: LazyProxy

    setup_confirm: Optional[
        Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[ACTION_DATA]]
    ]  # Returns filter data
    setup_message: Optional[Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[ActionSetupMessage]]] = None

    # Can use defaults for initial_setup
    name_id: str = "setup"
    icon: str = ""


class ModernActionABC(ABC, Generic[ACTION_DATA]):
    """Abstract base class for Sophie's modern actions.

    The modern approach is to make actions global and independent for the usage;
    thus they can be used both as Filter actions, Saveables buttons, warn actions, etc.
    """

    data_object: type[ACTION_DATA]
    name: str  # ID name would be a key-word of the action

    icon: str  # Emoji icon of the filter action
    title: LazyProxy  # Translate-able title of the filter

    interactive_setup: Optional[ModernActionSetting] = None  # Interactive setup of action
    default_data: Optional[ACTION_DATA] = None  # Default data

    as_filter: bool = True  # Can be used as a filter
    as_button: bool = False  # Can be used as a button
    as_flood: bool = False  # Can be used as an antiflood action
    allow_warns: bool = True  # Can be used as a warns action
    is_restrictive: bool = False  # Should suppress follow-up processing like AI autotranslation

    button_allowed_prefixes: Optional[tuple[str, ...]] = None  # Allowed prefixes for buttons

    def __init__(self) -> None:
        pass

    def settings(self, data: ACTION_DATA) -> dict[str, ModernActionSetting]:
        """Return the available settings for this action."""
        return {}

    @staticmethod
    @abstractmethod
    def description(data: ACTION_DATA) -> Element | str:
        raise NotImplementedError

    @abstractmethod
    async def handle(
        self, message: Message, data: dict, filter_data: ACTION_DATA
    ) -> Optional[Element | str | LazyProxy]:
        """Handle the action, returns the text of the actions done."""
        raise NotImplementedError
