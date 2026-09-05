"""Modern actions shared across Sophie modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar, cast

from aiogram.types import CallbackQuery, Message
from pydantic import BaseModel, ValidationError
from stfu_tg.doc import Element

from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.logger import log


class StoredAction(BaseModel):
    """Persisted action name and JSON data shared by action-owning models."""

    name: str
    data: dict[str, Any] | None = None


# What an action may hand back to its dispatcher.
ActionResult = Element | str | LazyProxy | Message | list[Message]

ACTION_DATA = TypeVar("ACTION_DATA", bound=BaseModel | None)


class ActionSetupTryAgainException(Exception):
    """Keep interactive action setup active after a user-correctable error."""


class ModernActionSetting[ACTION_DATA: BaseModel | None]:
    """A setting descriptor for a modern action."""

    title: LazyProxy
    setup_confirm: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[ACTION_DATA]] | None
    setup_message: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Element]] | None
    name_id: str
    icon: str

    def __init__(
        self,
        title: LazyProxy,
        setup_confirm: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[ACTION_DATA]] | None,
        setup_message: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Element]] | None = None,
        name_id: str = "setup",
        icon: str = "",
    ) -> None:
        self.title = title
        self.setup_confirm = setup_confirm
        self.setup_message = setup_message
        self.name_id = name_id
        self.icon = icon


class ModernActionABC(ABC, Generic[ACTION_DATA]):  # noqa: UP046
    """Abstract base class for reusable modern actions."""

    data_object: type[ACTION_DATA] | None = None
    name: str
    icon: str
    title: LazyProxy
    interactive_setup: ModernActionSetting | None = None
    default_data: ACTION_DATA | None = None
    as_filter: bool = True
    as_button: bool = False
    as_flood: bool = False
    allow_warns: bool = True
    skip_for_admins: bool = False
    button_allowed_prefixes: tuple[str, ...] | None = None

    def load_data(self, data: dict[str, Any] | BaseModel | None) -> ACTION_DATA:
        """Load persisted data, falling back to the action default when invalid."""
        if data is None or data == {}:
            return cast(ACTION_DATA, self.default_data)
        if isinstance(data, BaseModel):
            return cast(ACTION_DATA, data)
        if not isinstance(data, dict) or self.data_object is None:
            return cast(ACTION_DATA, self.default_data)
        try:
            data_type = cast(type[BaseModel], self.data_object)
            return cast(ACTION_DATA, data_type.model_validate(data))
        except (ValidationError, TypeError, ValueError):
            return cast(ACTION_DATA, self.default_data)

    async def execute(self, message: Message, data: dict, filter_data: ACTION_DATA) -> ActionResult | None:
        from sophie_bot.modules.utils_.admin import is_user_admin

        if self.skip_for_admins and message.from_user:
            user_tid = message.from_user.id
            if await is_user_globally_whitelisted(user_tid) or await is_user_admin(message.chat.id, user_tid):
                log.debug("Modern action: the sender is exempt, skipping...", action=self.name)
                return None
        return await self.handle(message, data, filter_data)

    def settings(self, data: ACTION_DATA) -> dict[str, ModernActionSetting]:
        """Return the available settings for this action."""
        return {}

    @staticmethod
    @abstractmethod
    def description(data: ACTION_DATA) -> Element | str:
        raise NotImplementedError

    @abstractmethod
    async def handle(self, message: Message, data: dict, filter_data: ACTION_DATA) -> ActionResult | None:
        """Handle the action and return its result."""
        raise NotImplementedError
