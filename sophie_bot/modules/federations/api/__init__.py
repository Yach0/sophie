from fastapi import APIRouter

from .federations import router as federations_router

api_router = APIRouter()
api_router.include_router(federations_router)

__all__ = ["api_router"]
