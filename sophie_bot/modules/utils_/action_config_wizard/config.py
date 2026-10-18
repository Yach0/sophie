from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiogram.types import CallbackQuery
from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.shared.actions import ActionDefinition
from sophie_bot.utils.i18n import LazyProxy

if TYPE_CHECKING:
    from sophie_bot.utils.handlers import SophieCallbackQueryHandler


class ActionDraft(BaseModel):
    actions: dict[str, dict[str, Any] | None] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionWizardConfig[DRAFT: ActionDraft]:
    scope: str
    title: str | LazyProxy
    done_message: str | LazyProxy
    max_actions: int
    draft_model: type[DRAFT]
    load_draft: Callable[[PydanticObjectId], Awaitable[DRAFT]] | None
    save_draft: Callable[[PydanticObjectId, DRAFT, CallbackQuery, ChatConnection], Awaitable[None]]
    min_actions: int = 0
    action_filter: Callable[[ActionDefinition[Any]], bool] | None = None
    on_back: Callable[[SophieCallbackQueryHandler, CallbackQuery], Awaitable[None]] | None = None
