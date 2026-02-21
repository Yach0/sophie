from __future__ import annotations

from typing import cast
from beanie import PydanticObjectId
from bson import DBRef


def normalize_chat_iids(chat_refs: list[object]) -> list[PydanticObjectId]:
    normalized: list[PydanticObjectId] = []
    for chat_ref in chat_refs:
        if isinstance(chat_ref, PydanticObjectId):
            normalized.append(chat_ref)
        elif isinstance(chat_ref, DBRef):
            normalized.append(cast(PydanticObjectId, chat_ref.id))
        elif isinstance(chat_ref, dict):
            dict_ref = cast(dict[str, object], chat_ref)
            chat_id = dict_ref.get("$id")
            if chat_id is not None:
                normalized.append(cast(PydanticObjectId, chat_id))
            else:
                normalized.append(cast(PydanticObjectId, chat_ref))
        else:
            normalized.append(cast(PydanticObjectId, chat_ref))
    return normalized
