from __future__ import annotations

from fastapi import APIRouter, Depends

from sophie_bot.constants import AI_FILTER_LIMIT_PER_CHAT
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.filters.utils_.legacy_filter_actions import LEGACY_FILTERS_ACTIONS
from sophie_bot.utils.api.auth import get_current_user

from .dependencies import require_filters_feature, require_filters_rest_api
from .schemas import FilterActionsCatalogResponse, FilterCatalogLimits, LegacyFilterActionCatalogItem
from .utils import MAX_FILTER_ACTIONS, build_filter_action_catalog

router = APIRouter(
    prefix="/filters/actions",
    tags=["filters"],
    dependencies=[Depends(require_filters_rest_api), Depends(require_filters_feature)],
)


@router.get("", response_model=FilterActionsCatalogResponse)
async def list_filter_actions(
    user: ChatModel = Depends(get_current_user),
) -> FilterActionsCatalogResponse:
    _ = user
    legacy_actions = [
        LegacyFilterActionCatalogItem(name=name, title=str(action["title"]))
        for name, action in sorted(LEGACY_FILTERS_ACTIONS.items())
    ]
    return FilterActionsCatalogResponse(
        limits=FilterCatalogLimits(
            max_actions_per_filter=MAX_FILTER_ACTIONS,
            max_ai_filters_per_chat=AI_FILTER_LIMIT_PER_CHAT,
        ),
        actions=build_filter_action_catalog(),
        legacy_actions=legacy_actions,
    )
