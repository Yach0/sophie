from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.middlewares.connections import ChatConnection

ResearchProgressStage = Literal["planning", "searching", "reviewing", "summarizing"]
ResearchProgressCallback = Callable[[ResearchProgressStage], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SophieAIToolContext:
    connection: ChatConnection
    chat_tid: int
    chat_iid: PydanticObjectId
    mode: AIMode = AIMode.support
    user_text: str | None = None
    research_progress_callback: ResearchProgressCallback | None = None
    user_tid: int | None = None  # Telegram user ID of the person who triggered this AI call
