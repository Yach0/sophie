from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import auth_router, groups_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(groups_router)

__all__ = ["api_router"]

__module_name__ = l_("REST API")
__module_emoji__ = "🔌"
__module_description__ = l_("REST API for external integrations")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Provides a REST API for external integrations and third-party applications."),
        l_("Allows programmatic access to bot functionality and data."),
    )
)

__exclude_public__ = True
