"""Reusable REST API feature flag gate dependencies.

Each dependency raises 403 if the corresponding feature flag is disabled,
providing a kill switch for individual REST API modules.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from sophie_bot.utils.feature_flags import FeatureType, is_enabled


def require_rest_feature(flag: FeatureType):
    """Factory that creates a FastAPI dependency checking a feature flag.

    Usage:
        router = APIRouter(dependencies=[Depends(require_rest_feature("notes_rest_api"))])
    """

    async def _gate() -> None:
        if not await is_enabled(flag):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Feature disabled")

    # Give the dependency a meaningful name for OpenAPI docs
    _gate.__name__ = f"require_{flag}"
    _gate.__qualname__ = f"require_{flag}"
    return _gate
