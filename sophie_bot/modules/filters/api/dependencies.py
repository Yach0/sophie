from __future__ import annotations

from fastapi import HTTPException, status

from sophie_bot.utils.feature_flags import is_enabled


async def require_filters_rest_api() -> None:
    if not await is_enabled("filters_rest_api"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Feature disabled")


async def require_filters_feature() -> None:
    if not await is_enabled("filters"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Filters feature is disabled")
