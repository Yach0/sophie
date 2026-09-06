"""End-to-end tests for troubleshooter commands."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.beta import BetaModeModel, CurrentMode, PreferredMode
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id


@pytest.mark.asyncio
async def test_instance_shows_current_and_preferred_modes(test_client: TestClient) -> None:
    admin, group, _user_model = await create_test_user_and_group(
        test_client,
        group_title="Instance Status Group",
    )
    await grant_admin(group.id, admin.id)
    chat_model = await ChatModel.get_by_tid(group.id)
    assert chat_model is not None
    await BetaModeModel.set_preferred_mode(chat_model.iid, PreferredMode.stable)
    await BetaModeModel.set_mode(chat_model.iid, CurrentMode.beta)

    requests = await test_client.send_command(command="instance", from_user=admin, chat=group)

    response_text = "\n".join(request.text or "" for request in requests)
    assert "Current instance" in response_text
    assert "Beta" in response_text
    assert "Preference" in response_text
    assert "Stable" in response_text


@pytest.mark.asyncio
async def test_instance_requires_admin(test_client: TestClient) -> None:
    _admin, group, _user_model = await create_test_user_and_group(
        test_client,
        group_title="Instance Auth Group",
    )
    stranger = test_client.create_user(
        user_id=next_user_id(),
        first_name="Stranger",
        username="instance_stranger",
    )
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)

    requests = await test_client.send_command(command="instance", from_user=stranger.user, chat=group)

    assert any("administrator" in (request.text or "").lower() for request in requests)
