from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from beanie import PydanticObjectId

from sophie_bot.middlewares.connections import ChatConnection

ResearchProgressStage = Literal["planning", "searching", "reviewing", "summarizing"]
ResearchProgressCallback = Callable[[ResearchProgressStage], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SophieAIToolContext:
    connection: ChatConnection
    chat_tid: int
    chat_iid: PydanticObjectId
    user_text: str | None = None
    research_progress_callback: ResearchProgressCallback | None = None
