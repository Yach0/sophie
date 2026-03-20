from __future__ import annotations

from typing import Literal

from sophie_bot.db.models import FiltersModel, LocksModel
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type

ConflictOwner = Literal["locks", "filters"]


async def get_lock_type_owner(chat_iid, lock_type: str) -> ConflictOwner | None:
    if not is_supported_lock_type(lock_type):
        return None

    locks_model = await LocksModel.find_one(LocksModel.chat.id == chat_iid)
    if locks_model and lock_type in locks_model.locked_types:
        return "locks"

    filter_model = await FiltersModel.find_one(FiltersModel.chat.id == chat_iid, FiltersModel.handler == lock_type)
    if filter_model and filter_model.effective_version >= 2:
        return "filters"

    return None


async def get_filter_lock_types(chat_iid) -> list[FiltersModel]:
    filters = await FiltersModel.find(FiltersModel.chat.id == chat_iid).to_list()
    return [
        filter_item
        for filter_item in filters
        if filter_item.effective_version >= 2 and is_supported_lock_type(filter_item.handler)
    ]
