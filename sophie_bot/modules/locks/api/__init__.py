from __future__ import annotations

from fastapi import APIRouter

from .get import router as get_router
from .lockable import router as lockable_router
from .put import router as put_router

api_router = APIRouter(prefix="/locks", tags=["locks"])
api_router.include_router(get_router)
api_router.include_router(put_router)
api_router.include_router(lockable_router)
