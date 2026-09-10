from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.rest import get_services
from sophie_bot.utils.feature_flags import is_enabled


async def require_filters_feature(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> None:
    if not await is_enabled("filters", redis=services.redis):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Filters feature is disabled")
