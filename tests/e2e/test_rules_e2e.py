"""End-to-end tests for the rules module: /setrules, /rules, /resetrules."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models import ChatModel, RulesModel
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id


async def _rules_text(group_tid: int) -> str | None:
    chat = await ChatModel.get_by_tid(group_tid)
    assert chat is not None
    rules = await RulesModel.get_rules(chat.iid)
    return rules.text if rules else None


@pytest.mark.asyncio
async def test_setrules_persists_and_rules_shows_them(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Rules Group")
    await grant_admin(group.id, admin.id)

    set_requests = await test_client.send_command(
        command="setrules", from_user=admin, args="Be kind and stay on topic", chat=group
    )
    assert set_requests, "Bot should acknowledge /setrules"
    assert await _rules_text(group.id) == "Be kind and stay on topic"

    show_requests = await test_client.send_command(command="rules", from_user=admin, chat=group)
    assert any("Be kind and stay on topic" in (request.text or "") for request in show_requests)


@pytest.mark.asyncio
async def test_setrules_requires_admin(test_client: TestClient) -> None:
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="Rules Auth Group")
    stranger = test_client.create_user(user_id=next_user_id(), first_name="Stranger", username="rules_stranger")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)

    requests = await test_client.send_command(command="setrules", from_user=stranger.user, args="my rules", chat=group)

    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert await _rules_text(group.id) is None


@pytest.mark.asyncio
async def test_rules_reports_when_none_set(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Rules Empty Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="rules", from_user=admin, chat=group)

    assert any("no rules" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_resetrules_clears_them(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Rules Reset Group")
    await grant_admin(group.id, admin.id)
    await test_client.send_command(command="setrules", from_user=admin, args="Temporary rules", chat=group)
    assert await _rules_text(group.id) == "Temporary rules"

    requests = await test_client.send_command(command="resetrules", from_user=admin, chat=group)

    assert any("reset" in (request.text or "").lower() for request in requests)
    assert await _rules_text(group.id) is None
