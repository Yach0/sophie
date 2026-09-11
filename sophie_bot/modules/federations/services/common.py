from __future__ import annotations

from collections.abc import Mapping

from beanie import PydanticObjectId
from bson import DBRef, ObjectId


def normalize_chat_iids(chat_refs: list[object]) -> list[PydanticObjectId]:
    normalized: list[PydanticObjectId] = []
    for chat_ref in chat_refs:
        chat_id: object
        if isinstance(chat_ref, DBRef):
            chat_id = chat_ref.id
        elif isinstance(chat_ref, Mapping):
            chat_id = chat_ref.get("$id")
        else:
            chat_id = chat_ref

        if isinstance(chat_id, PydanticObjectId):
            normalized.append(chat_id)
        elif isinstance(chat_id, ObjectId):
            normalized.append(PydanticObjectId(chat_id))
        else:
            raise TypeError(f"Unsupported chat reference type: {type(chat_ref).__name__}")
    return normalized
