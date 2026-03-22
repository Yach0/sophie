from fastapi import APIRouter

from .actions import router as actions_router
from .filters import router as filters_router

api_router = APIRouter()
api_router.include_router(filters_router)
api_router.include_router(actions_router)

__all__ = ["api_router"]
