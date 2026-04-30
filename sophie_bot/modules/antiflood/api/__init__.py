from fastapi import APIRouter, Depends

from sophie_bot.utils.api.feature_gate import require_rest_feature

from .actions import router as actions_router
from .antiflood import router as antiflood_router

api_router = APIRouter(dependencies=[Depends(require_rest_feature("antiflood_rest_api"))])
api_router.include_router(antiflood_router)
api_router.include_router(actions_router)

__all__ = ["api_router"]
