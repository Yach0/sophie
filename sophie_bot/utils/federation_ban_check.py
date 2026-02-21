from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import In
from bson import DBRef

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation, FederationBan

FederationBanScope = Literal["current", "subscribed"]


@dataclass(frozen=True)
class FederationBanInfo:
    scope: FederationBanScope
    fed_name: str
    fed_id: str


async def get_user_federation_ban_info(chat_iid: PydanticObjectId, user_tid: int) -> FederationBanInfo | None:
    federation = await _get_federation_for_chat(chat_iid)
    if not federation:
        return None

    federation_chain_ids = await _get_subscription_chain(federation.fed_id)
    federation_chain_ids.append(federation.fed_id)

    existing_ban = await FederationBan.find(
        In(FederationBan.fed_id, federation_chain_ids),
        FederationBan.user_id == user_tid,
    ).first_or_none()
    if not existing_ban:
        return None

    if existing_ban.fed_id == federation.fed_id:
        return FederationBanInfo(scope="current", fed_name=federation.fed_name, fed_id=federation.fed_id)

    banning_federation = await Federation.find_one(Federation.fed_id == existing_ban.fed_id)
    banning_fed_name = banning_federation.fed_name if banning_federation else existing_ban.fed_id
    return FederationBanInfo(scope="subscribed", fed_name=banning_fed_name, fed_id=existing_ban.fed_id)


async def _get_federation_for_chat(chat_iid: PydanticObjectId) -> Federation | None:
    direct_match = await Federation.find_one(Federation.chats == DBRef(ChatModel.Settings.name, chat_iid))
    if direct_match:
        return direct_match

    async for federation in Federation.find_all():
        if not federation.chats:
            continue
        normalized_chat_iids = _normalize_chat_iids([chat_link.to_ref() for chat_link in federation.chats])
        if chat_iid in normalized_chat_iids:
            return federation

    return None


def _normalize_chat_iids(chat_refs: list[object]) -> list[PydanticObjectId]:
    normalized: list[PydanticObjectId] = []
    for chat_ref in chat_refs:
        if isinstance(chat_ref, PydanticObjectId):
            normalized.append(chat_ref)
            continue

        if isinstance(chat_ref, DBRef):
            normalized.append(chat_ref.id)
            continue

        if isinstance(chat_ref, dict):
            chat_ref_dict = cast(dict[str, object], chat_ref)
            reference_id = chat_ref_dict.get("$id")
            if isinstance(reference_id, PydanticObjectId):
                normalized.append(reference_id)
            continue

    return normalized


async def _get_subscription_chain(fed_id: str) -> list[str]:
    chain_ids: list[str] = []
    pending_federation_ids: list[str] = [fed_id]
    visited_federation_ids: set[str] = set()

    while pending_federation_ids:
        current_fed_id = pending_federation_ids.pop()
        if current_fed_id in visited_federation_ids:
            continue

        visited_federation_ids.add(current_fed_id)
        current_federation = await Federation.find_one(Federation.fed_id == current_fed_id)
        if not current_federation or not current_federation.subscribed:
            continue

        for subscribed_fed_id in current_federation.subscribed:
            if subscribed_fed_id in visited_federation_ids:
                continue

            pending_federation_ids.append(subscribed_fed_id)
            if subscribed_fed_id != fed_id:
                chain_ids.append(subscribed_fed_id)

    return chain_ids
