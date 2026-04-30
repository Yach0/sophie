from fastapi import APIRouter, Depends

from sophie_bot.utils.api.feature_gate import require_rest_feature

from .moderator import router as moderator_router

api_router = APIRouter(dependencies=[Depends(require_rest_feature("ai_moderator_rest_api"))])
api_router.include_router(moderator_router)
