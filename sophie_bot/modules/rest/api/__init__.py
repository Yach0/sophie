from .auth import router as auth_router
from .feature_flags import router as feature_flags_router
from .groups import router as groups_router
from .telegram_media import router as telegram_media_router

__all__ = ["auth_router", "feature_flags_router", "groups_router", "telegram_media_router"]
