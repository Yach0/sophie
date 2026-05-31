from __future__ import annotations

from fastapi import APIRouter, Depends

from .dependencies import require_federations_rest_api
from .routers import bans_router, chats_router, manage_router, subscriptions_router

router = APIRouter(
    prefix="/federations",
    tags=["federations"],
    dependencies=[Depends(require_federations_rest_api)],
)

router.include_router(manage_router)
router.include_router(chats_router)
router.include_router(bans_router)
router.include_router(subscriptions_router)
