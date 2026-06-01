from __future__ import annotations

from dataclasses import dataclass

from beanie import PydanticObjectId

from sophie_bot.middlewares.connections import ChatConnection


@dataclass(frozen=True, slots=True)
class SophieAIToolContext:
    connection: ChatConnection
    chat_tid: int
    chat_iid: PydanticObjectId
    user_text: str | None = None
