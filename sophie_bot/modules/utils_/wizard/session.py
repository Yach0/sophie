from __future__ import annotations

import time
from secrets import token_urlsafe
from typing import Any

from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from beanie import PydanticObjectId

from sophie_bot.constants import ACW_SESSION_TTL_SECONDS

_WIZARD_KEY = "wizard"


class WizardFSM(StatesGroup):
    interactive_input = State()


class WizardSession:
    def __init__(self, state: FSMContext, scope: str, session_id: str | None = None) -> None:
        self.state = state
        self.scope = scope
        self.session_id = session_id

    async def start(self, chat_iid: PydanticObjectId, draft: dict[str, Any]) -> str:
        session_id = token_urlsafe(6)
        data = await self.state.get_data()
        data[_WIZARD_KEY] = {
            "scope": self.scope,
            "session_id": session_id,
            "chat_iid": str(chat_iid),
            "started_at": time.time(),
            "draft": draft,
        }
        await self.state.set_data(data)
        await self.state.set_state(None)
        self.session_id = session_id
        return session_id

    async def is_active(self, chat_iid: PydanticObjectId | None = None) -> bool:
        wizard = await self._owned_wizard()
        if wizard is None:
            return False
        if chat_iid is not None and wizard.get("chat_iid") != str(chat_iid):
            return False
        started_at = wizard.get("started_at")
        return isinstance(started_at, (int, float)) and time.time() - started_at <= ACW_SESSION_TTL_SECONDS

    async def get_draft(self) -> dict[str, Any] | None:
        wizard = await self._owned_wizard()
        if wizard is None:
            return None
        draft = wizard.get("draft")
        return dict(draft) if isinstance(draft, dict) else None

    async def set_draft(self, draft: dict[str, Any]) -> None:
        wizard = await self._require_owned_wizard()
        wizard["draft"] = draft
        await self.state.update_data(**{_WIZARD_KEY: wizard})

    async def start_input(self, **context: Any) -> None:
        wizard = await self._require_owned_wizard()
        wizard["input"] = context
        await self.state.update_data(**{_WIZARD_KEY: wizard})
        await self.state.set_state(WizardFSM.interactive_input)

    async def get_input_context(self) -> dict[str, Any] | None:
        wizard = await self._owned_wizard()
        if wizard is None:
            return None
        context = wizard.get("input")
        return dict(context) if isinstance(context, dict) else None

    async def clear_input(self) -> bool:
        wizard = await self._owned_wizard()
        if wizard is None:
            return False
        wizard.pop("input", None)
        await self.state.update_data(**{_WIZARD_KEY: wizard})
        if await self.state.get_state() == WizardFSM.interactive_input.state:
            await self.state.set_state(None)
        return True

    async def clear(self) -> bool:
        wizard = await self._owned_wizard()
        if wizard is None:
            return False
        data = await self.state.get_data()
        data.pop(_WIZARD_KEY, None)
        await self.state.set_data(data)
        if await self.state.get_state() == WizardFSM.interactive_input.state:
            await self.state.set_state(None)
        return True

    def require_session_id(self) -> str:
        if self.session_id is None:
            raise TypeError("No wizard session identifier")
        return self.session_id

    async def _owned_wizard(self) -> dict[str, Any] | None:
        data = await self.state.get_data()
        wizard = data.get(_WIZARD_KEY)
        if not isinstance(wizard, dict) or wizard.get("scope") != self.scope:
            return None
        stored_session_id = wizard.get("session_id")
        if not isinstance(stored_session_id, str) or not stored_session_id:
            return None
        if self.session_id is not None and stored_session_id != self.session_id:
            return None
        self.session_id = stored_session_id
        return wizard

    async def _require_owned_wizard(self) -> dict[str, Any]:
        wizard = await self._owned_wizard()
        if wizard is None:
            raise TypeError("No matching wizard session")
        return wizard


class WizardScopeFilter(BaseFilter):
    def __init__(self, scope: str) -> None:
        self.scope = scope

    async def __call__(self, event_or_state: Any, state: FSMContext | None = None) -> bool:
        actual_state = event_or_state if state is None else state
        if not hasattr(actual_state, "get_data"):
            return False
        data = await actual_state.get_data()
        wizard = data.get(_WIZARD_KEY)
        return isinstance(wizard, dict) and wizard.get("scope") == self.scope
