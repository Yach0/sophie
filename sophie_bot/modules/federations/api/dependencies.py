from __future__ import annotations

from fastapi import HTTPException, status

from sophie_bot.utils.feature_flags import is_enabled


async def require_federations_rest_api() -> None:
    if not await is_enabled("feds_rest_api"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Feature disabled")
