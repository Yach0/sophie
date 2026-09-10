from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.chat import UserInGroupModel

if TYPE_CHECKING:
    from sophie_bot.middlewares.connections import ChatConnection


@dataclass(slots=True)
class RequestContext:
    event_chat: ChatModel | None = None
    target_chat: ChatModel | None = None
    actor: ChatModel | None = None
    connection: ChatConnection | None = None
    user_in_group: UserInGroupModel | None = None
