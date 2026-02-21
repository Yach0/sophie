from __future__ import annotations

from .bans import router as bans_router
from .chats import router as chats_router
from .manage import router as manage_router
from .subscriptions import router as subscriptions_router

__all__ = ["bans_router", "chats_router", "manage_router", "subscriptions_router"]
