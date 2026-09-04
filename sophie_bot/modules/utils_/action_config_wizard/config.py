from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiogram.types import CallbackQuery
from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.shared.actions import ModernActionABC, StoredAction
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
    action_filter: Callable[[ModernActionABC[Any]], bool] | None = None
    on_back: Callable[[SophieCallbackQueryHandler, CallbackQuery], Awaitable[None]] | None = None


from sophie_bot.modules.utils_.action_config_wizard.wizard import ActionWizard


def model_action_wizard(
    *,
    model_loader: Callable[[PydanticObjectId], Awaitable[Any]],
    attribute: str,
    scope: str,
    title: str | LazyProxy,
    done_message: str | LazyProxy,
    max_actions: int,
    min_actions: int = 0,
    action_filter: Callable[[ModernActionABC[Any]], bool] | None = None,
    on_back: Callable[[SophieCallbackQueryHandler, CallbackQuery], Awaitable[None]] | None = None,
) -> ActionWizard[ActionDraft]:
    if max_actions <= 0:
        raise ValueError("max_actions must be positive")
    if min_actions < 0 or min_actions > max_actions:
        raise ValueError("min_actions must be between zero and max_actions")

    async def load_draft(chat_iid: PydanticObjectId) -> ActionDraft:
        model = await model_loader(chat_iid)
        actions = getattr(model, attribute, []) or []
        return ActionDraft(actions={action.name: action.data for action in actions})

    async def save_draft(
        chat_iid: PydanticObjectId,
        draft: ActionDraft,
        callback_query: CallbackQuery,
        connection: ChatConnection,
    ) -> None:
        del callback_query, connection
        if len(draft.actions) > max_actions:
            raise ValueError(f"Draft exceeds maximum allowed actions ({max_actions})")
        model = await model_loader(chat_iid)
        setattr(model, attribute, [StoredAction(name=name, data=data) for name, data in draft.actions.items()])
        await model.save()

    config = ActionWizardConfig(
        scope=scope,
        title=title,
        done_message=done_message,
        max_actions=max_actions,
        min_actions=min_actions,
        draft_model=ActionDraft,
        load_draft=load_draft,
        save_draft=save_draft,
        action_filter=action_filter,
        on_back=on_back,
    )

    return ActionWizard(config)
