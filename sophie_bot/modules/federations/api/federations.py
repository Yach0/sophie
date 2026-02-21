from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.utils.feature_flags import is_enabled

from .dependencies import require_federations_rest_api
from .routers import bans_router, chats_router, manage_router, subscriptions_router


async def require_federations_feature_flag():
    """Require new_feds feature flag to be enabled."""
    if not await is_enabled("new_feds"):
        raise HTTPException(status_code=503, detail="Federations feature is disabled")


router = APIRouter(
    prefix="/federations",
    tags=["federations"],
    dependencies=[Depends(require_federations_rest_api), Depends(require_federations_feature_flag)],
)

router.include_router(manage_router)
router.include_router(chats_router)
router.include_router(bans_router)
router.include_router(subscriptions_router)
