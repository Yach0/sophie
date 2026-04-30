from fastapi import APIRouter, Depends

from sophie_bot.utils.api.feature_gate import require_rest_feature

from .delete_warn import router as delete_warn_router
from .get_settings import router as get_settings_router
from .get_user_warns import router as get_user_warns_router
from .update_settings import router as update_settings_router

api_router = APIRouter(dependencies=[Depends(require_rest_feature("warns_rest_api"))])
api_router.include_router(get_settings_router)
api_router.include_router(update_settings_router)
api_router.include_router(get_user_warns_router)
api_router.include_router(delete_warn_router)
