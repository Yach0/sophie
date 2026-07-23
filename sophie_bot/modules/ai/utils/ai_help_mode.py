from __future__ import annotations

from aiogram.fsm.context import FSMContext

# Sophie-help is a detour inside one AI session, not a setting: it lives in the FSM state next to
# AiPMFSM.in_ai, so leaving the AI mode leaves it behind with no separate expiry to reason about.
_HELP_MODE_KEY = "ai_help_mode"


async def set_help_mode(state: FSMContext, enabled: bool) -> None:
    await state.update_data({_HELP_MODE_KEY: enabled})


async def is_help_mode(state: FSMContext | None) -> bool:
    if state is None:
        return False
    return bool((await state.get_data()).get(_HELP_MODE_KEY, False))
