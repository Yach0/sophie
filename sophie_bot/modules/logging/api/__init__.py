from fastapi import APIRouter, Depends

from sophie_bot.utils.api.feature_gate import require_rest_feature

from .logs import router as logs_router

api_router = APIRouter(dependencies=[Depends(require_rest_feature("logging_rest_api"))])
api_router.include_router(logs_router)
