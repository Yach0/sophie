from __future__ import annotations

from fastapi import APIRouter

from .routers import bans_router, chats_router, manage_router, subscriptions_router

router = APIRouter(
    prefix="/federations",
    tags=["federations"],
)

router.include_router(manage_router)
router.include_router(chats_router)
router.include_router(bans_router)
router.include_router(subscriptions_router)
