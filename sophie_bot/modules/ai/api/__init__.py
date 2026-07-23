from fastapi import APIRouter

from .catalog import router as catalog_router
from .moderator import router as moderator_router

api_router = APIRouter()
api_router.include_router(moderator_router)
api_router.include_router(catalog_router)
