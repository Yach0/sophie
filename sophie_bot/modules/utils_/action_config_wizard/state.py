from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from enum import Enum
from typing import Any, Optional, Tuple

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from beanie import PydanticObjectId
from bson.errors import InvalidId


class ActionConfigFSM(StatesGroup):
    """FSM states for action configuration with interactive setup."""

    interactive_setup = State()


# Session TTL
ACW_SESSION_TTL_SECONDS = 20 * 60  # 20 minutes

# FSM data keys
_K_MODULE = "acw_module"
_K_CHAT_IID = "acw_chat_iid"
_K_STARTED_AT = "acw_started_at"
_K_ACTION_NAME = "acw_action_name"
_K_ACTION_DATA = "acw_action_data"
_SETUP_CONTEXT_KEYS = (
    "action_setup_name",
    "action_setup_chat_tid",
    "action_setup_callback_prefix",
    "setting_setup_action",
    "setting_setup_setting_id",
    "setting_setup_chat_tid",
    "setting_setup_callback_prefix",
)


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects to JSON-safe types."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dt_time):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        try:
            return obj.total_seconds()
        except (OverflowError, ValueError):
            return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except (AttributeError, TypeError, ValueError):
            try:
                return obj.dict()
            except (AttributeError, TypeError):
                return str(obj)
    if isinstance(obj, dict):
        return {str(key): _sanitize_for_json(val) for key, val in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_sanitize_for_json(val) for val in obj]
    return str(obj)


@dataclass
class WizardState:
    """Typed wrapper around FSMContext for Action Config Wizard session data.

    Provides type-safe accessors instead of raw string-key dict operations.
    """

    _state: FSMContext

    # -- session management --------------------------------------------------

    async def ensure_session(self, module_name: str, chat_iid: PydanticObjectId) -> None:
        """Ensure a valid session exists; start or reset if needed."""
        data = await self._state.get_data()
        started_at = data.get(_K_STARTED_AT)
        stored_chat_iid = data.get(_K_CHAT_IID)
        same_context = data.get(_K_MODULE) == module_name and stored_chat_iid == str(chat_iid)

        if (not same_context) or self._expired(started_at):
            data[_K_MODULE] = module_name
            data[_K_CHAT_IID] = str(chat_iid)
            data[_K_STARTED_AT] = time.time()
            data.pop(_K_ACTION_NAME, None)
            data.pop(_K_ACTION_DATA, None)
            await self._state.update_data(**data)

    async def clear(self) -> None:
        """Clear all ACW session keys from FSM data."""
        data = await self._state.get_data()
        for key in (_K_MODULE, _K_CHAT_IID, _K_STARTED_AT, _K_ACTION_NAME, _K_ACTION_DATA):
            data.pop(key, None)
        await self._state.update_data(**data)

    async def replace_setup_context(self, **kwargs: Any) -> None:
        """Replace setup-specific context keys so stale mode data cannot leak across flows."""
        data = await self._state.get_data()
        for key in _SETUP_CONTEXT_KEYS:
            data.pop(key, None)
        data.update(kwargs)
        await self._state.update_data(**data)

    async def is_active(self, module_name: str, chat_iid: PydanticObjectId) -> bool:
        """Return True if session is alive for the given module and chat."""
        data = await self._state.get_data()
        if data.get(_K_MODULE) != module_name or data.get(_K_CHAT_IID) != str(chat_iid):
            return False
        return not self._expired(data.get(_K_STARTED_AT))

    # -- staged action -------------------------------------------------------

    async def set_action(self, action_name: str) -> None:
        data = await self._state.get_data()
        data[_K_ACTION_NAME] = action_name
        await self._state.update_data(**data)

    async def set_action_data(self, action_data: dict[str, Any] | None) -> None:
        data = await self._state.get_data()
        data[_K_ACTION_DATA] = _sanitize_for_json(action_data or {})
        await self._state.update_data(**data)

    async def stage_action(
        self,
        module_name: str,
        chat_iid: PydanticObjectId,
        action_name: str,
        action_data: Optional[dict[str, Any]] = None,
    ) -> None:
        """Ensure session and stage the selected action without persisting."""
        await self.ensure_session(module_name, chat_iid)
        await self.set_action(action_name)
        await self.set_action_data(action_data or {})

    async def get_staged(self) -> Tuple[Optional[PydanticObjectId], Optional[str], Optional[dict[str, Any]]]:
        """Return (chat_iid, action_name, action_data) from staged session."""
        data = await self._state.get_data()
        chat_iid_raw = data.get(_K_CHAT_IID)
        chat_iid: Optional[PydanticObjectId]
        try:
            chat_iid = PydanticObjectId(chat_iid_raw) if chat_iid_raw else None
        except (InvalidId, TypeError):
            chat_iid = None
        action_name = data.get(_K_ACTION_NAME)
        action_data = data.get(_K_ACTION_DATA)
        return chat_iid, action_name, action_data

    async def has_staged_changes(self, module_name: str, chat_iid: PydanticObjectId) -> bool:
        """Return True if there is staged action data for this module and chat."""
        if not await self.is_active(module_name, chat_iid):
            return False
        staged_chat_iid, action_name, _ = await self.get_staged()
        return bool(action_name) and (staged_chat_iid == chat_iid)

    # -- FSM state delegation ------------------------------------------------

    async def set_fsm_state(self, state_value: State | None) -> None:
        await self._state.set_state(state_value)

    async def update_data(self, **kwargs: Any) -> None:
        await self._state.update_data(**kwargs)

    async def get_data(self) -> dict[str, Any]:
        return await self._state.get_data()

    async def clear_fsm(self) -> None:
        await self._state.clear()

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _expired(started_at: Optional[float]) -> bool:
        if started_at is None:
            return True
        return (time.time() - started_at) > ACW_SESSION_TTL_SECONDS
