from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from beanie import PydanticObjectId

from sophie_bot.db.models.filters import FilterActionType

from .config import ActionWizardDraft


class PersistedModel(Protocol):
    async def save(self) -> Any: ...


class ModelActionsContext:
    """Adapt a model list field to the aggregate wizard contract."""

    def __init__(
        self,
        model_loader: Callable[[PydanticObjectId], Awaitable[PersistedModel]],
        attribute: str,
        *,
        maximum_actions: int | None = None,
        metadata_loader: Callable[[PersistedModel], dict[str, Any]] | None = None,
    ) -> None:
        self._model_loader = model_loader
        self._attribute = attribute
        self._maximum_actions = maximum_actions
        self._metadata_loader = metadata_loader

    async def load(self, chat_iid: PydanticObjectId) -> ActionWizardDraft:
        model = await self._model_loader(chat_iid)
        actions = getattr(model, self._attribute, []) or []
        return ActionWizardDraft(
            actions={action.name: action.data for action in actions},
            metadata=self._metadata_loader(model) if self._metadata_loader else {},
        )

    async def validate(
        self,
        chat_iid: PydanticObjectId,
        draft: ActionWizardDraft,
        event: Any = None,
        connection: Any = None,
    ) -> None:
        del chat_iid, event, connection
        if self._maximum_actions is not None and len(draft.actions) > self._maximum_actions:
            raise ValueError(f"At most {self._maximum_actions} actions may be configured")

    async def commit(
        self,
        chat_iid: PydanticObjectId,
        draft: ActionWizardDraft,
        event: Any = None,
        connection: Any = None,
    ) -> None:
        del event, connection
        model = await self._model_loader(chat_iid)
        actions = [FilterActionType(name=name, data=data or {}) for name, data in draft.actions.items()]
        setattr(model, self._attribute, actions)
        await model.save()

    def update_control(self, draft: ActionWizardDraft, control_name: str) -> bool:
        del draft, control_name
        return False

    def render_details(self, draft: ActionWizardDraft) -> list[tuple[str, str]]:
        del draft
        return []

    def render_controls(self, draft: ActionWizardDraft, callback_prefix: str) -> list[list[Any]]:
        del draft, callback_prefix
        return []


__all__ = ["ModelActionsContext"]
