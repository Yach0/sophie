from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import auth_router, feature_flags_router, groups_router, telegram_media_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(feature_flags_router)
api_router.include_router(groups_router)
api_router.include_router(telegram_media_router)

__all__ = ["api_router"]


module_manifest = ModuleManifest(
    name="rest",
    api_router=api_router,
    title=l_("REST API"),
    emoji="🔌",
    description=l_("REST API for external integrations"),
    info=LazyProxy(
        lambda: Doc(
            l_("Provides a REST API for external integrations and third-party applications."),
            l_("Allows programmatic access to bot functionality and data."),
        )
    ),
    exclude_public=True,
)
