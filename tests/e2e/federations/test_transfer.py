"""E2E tests for federation ownership transfer: /transferfed + /accepttransfer."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.federations.services import FederationManageService
from tests.e2e.federations.conftest import create_federation_via_command
from tests.e2e.helpers import create_test_user_and_group, grant_admin


async def _fed_with_candidate(
    test_client: TestClient, *, owner_tid: int, chat_tid: int, candidate_tid: int, fed_name: str
):
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client, user_id=owner_tid, chat_id=chat_tid, first_name="XferOwner", group_title=fed_name
    )
    await grant_admin(group.id, owner_user.id, creator=True)
    candidate = test_client.create_user(user_id=candidate_tid, first_name="Heir", username=f"heir_{candidate_tid}")
    # The candidate must have started the bot (private ChatModel) to receive ownership.
    await test_client.send_message(text="init", from_user=candidate.user, chat=group)
    await ChatModel.upsert_user(candidate.user)

    federation = await create_federation_via_command(test_client, owner_user, group, fed_name, owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)
    return owner_user, group, federation, candidate.user


@pytest.mark.asyncio
async def test_transfer_then_accept_flips_owner(test_client: TestClient) -> None:
    owner_user, group, federation, heir = await _fed_with_candidate(
        test_client, owner_tid=6060, chat_tid=-1001000006060, candidate_tid=6061, fed_name="Transfer Fed"
    )

    sent = await test_client.send_command(
        command="transferfed", from_user=owner_user, args=f"{federation.fed_id} {heir.id}", chat=group
    )
    assert any("transfer request sent" in (request.text or "").lower() for request in sent)

    accepted = await test_client.send_command(
        command="accepttransfer", from_user=heir, args=federation.fed_id, chat=group
    )
    assert any("now the owner" in (request.text or "").lower() for request in accepted)

    updated = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated is not None
    new_owner = await updated.creator.fetch()
    assert new_owner is not None and new_owner.tid == heir.id


@pytest.mark.asyncio
async def test_accept_by_wrong_user_is_rejected(test_client: TestClient) -> None:
    owner_user, group, federation, heir = await _fed_with_candidate(
        test_client, owner_tid=6062, chat_tid=-1001000006062, candidate_tid=6063, fed_name="Transfer Auth Fed"
    )
    await test_client.send_command(
        command="transferfed", from_user=owner_user, args=f"{federation.fed_id} {heir.id}", chat=group
    )

    intruder = test_client.create_user(user_id=6064, first_name="NotTheHeir", username="not_heir")
    await test_client.send_message(text="init", from_user=intruder.user, chat=group)

    requests = await test_client.send_command(
        command="accepttransfer", from_user=intruder.user, args=federation.fed_id, chat=group
    )
    assert any("not for you" in (request.text or "").lower() for request in requests)

    unchanged = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert unchanged is not None
    owner = await unchanged.creator.fetch()
    assert owner is not None and owner.tid == owner_user.id


@pytest.mark.asyncio
async def test_transfer_by_non_owner_is_rejected(test_client: TestClient) -> None:
    _owner, group, federation, heir = await _fed_with_candidate(
        test_client, owner_tid=6065, chat_tid=-1001000006065, candidate_tid=6066, fed_name="Transfer NonOwner Fed"
    )

    requests = await test_client.send_command(
        command="transferfed", from_user=heir, args=f"{federation.fed_id} {heir.id}", chat=group
    )
    assert any("owner" in (request.text or "").lower() for request in requests)
