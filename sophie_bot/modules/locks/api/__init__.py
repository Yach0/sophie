from __future__ import annotations

from fastapi import APIRouter, Depends

from sophie_bot.utils.api.feature_gate import require_rest_feature

from .get import router as get_router
from .lockable import router as lockable_router
from .put import router as put_router

api_router = APIRouter(
    prefix="/locks",
    tags=["locks"],
    dependencies=[Depends(require_rest_feature("locks_rest_api"))],
)
api_router.include_router(get_router)
api_router.include_router(put_router)
api_router.include_router(lockable_router)
