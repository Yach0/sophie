"""End-to-end tests for antiflood enforcement and its config commands.

The enforcer is a module middleware, so consecutive messages from one member run through it.
The default action mutes (restrictChatMember) once the per-user message count is reached.
"""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.modules.utils_.wizard import WizardCallback
from sophie_bot.utils.feature_flags import set_enabled
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id


async def _group_with_flood(test_client: TestClient, *, message_count: int) -> tuple[object, object]:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Antiflood Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AntifloodModel(chat=chat.iid, enabled=True, message_count=message_count).save()

    member = test_client.create_user(user_id=next_user_id(), first_name="Flooder", username="flooder")
    await test_client.send_message(text="init", from_user=member.user, chat=group)
    return group, member.user


@pytest.mark.asyncio
async def test_flooding_triggers_the_action(test_client: TestClient) -> None:
    group, member = await _group_with_flood(test_client, message_count=3)

    restricts: list = []
    for index in range(3):
        requests = await test_client.send_message(text=f"spam {index}", from_user=member, chat=group)
        restricts += [
            request
            for request in requests
            if request.request_type == RequestType.RESTRICT_CHAT_MEMBER and request.params.get("user_id") == member.id
        ]

    assert restricts, "Reaching the flood threshold should restrict the flooding user"


@pytest.mark.asyncio
async def test_below_threshold_is_not_restricted(test_client: TestClient) -> None:
    group, member = await _group_with_flood(test_client, message_count=5)

    restricts: list = []
    for index in range(3):
        requests = await test_client.send_message(text=f"chatter {index}", from_user=member, chat=group)
        restricts += [request for request in requests if request.request_type == RequestType.RESTRICT_CHAT_MEMBER]

    assert not restricts, "Staying under the threshold must not restrict anyone"


@pytest.mark.asyncio
async def test_admin_is_exempt_from_antiflood(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Antiflood Admin Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AntifloodModel(chat=chat.iid, enabled=True, message_count=2).save()

    restricts: list = []
    for index in range(4):
        requests = await test_client.send_message(text=f"admin msg {index}", from_user=admin, chat=group)
        restricts += [request for request in requests if request.request_type == RequestType.RESTRICT_CHAT_MEMBER]

    assert not restricts, "Admins are exempt from antiflood enforcement"


@pytest.mark.asyncio
async def test_antiflood_count_command_persists(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Antiflood Count Group")
    await grant_admin(group.id, admin.id)

    await test_client.send_command(command="antiflood_count", from_user=admin, args="7", chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    settings = await AntifloodModel.get_by_chat_iid(chat.iid)
    assert settings.message_count == 7


@pytest.mark.asyncio
async def test_enableantiflood_command_persists(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Antiflood Enable Group")
    await grant_admin(group.id, admin.id)

    await test_client.send_command(command="enableantiflood", from_user=admin, args="on", chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    settings = await AntifloodModel.get_by_chat_iid(chat.iid)
    assert settings.enabled is True

@pytest.mark.asyncio
async def test_antiflood_action_is_silent_when_wizard_flag_is_disabled(test_client: TestClient) -> None:
    await set_enabled("action_config_wizard", False)
    try:
        admin, group, _model = await create_test_user_and_group(
            test_client, group_title="Antiflood Wizard Disabled Group"
        )
        await grant_admin(group.id, admin.id)

        requests = await test_client.send_command(command="antiflood_action", from_user=admin, chat=group)
        assert not requests
    finally:
        await set_enabled("action_config_wizard", True)


@pytest.mark.asyncio
async def test_antiflood_action_configures_action_and_ignores_plain_messages(
    test_client: TestClient,
) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Antiflood Wizard Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    await test_client.send_command(command="antiflood_action", from_user=admin, chat=group)
    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    wizard_message = MessageFactory.create(text="Wizard", from_user=bot_user, chat=group)

    await test_client.send_callback(
        WizardCallback(scope="antiflood_action", op="add").pack(),
        from_user=admin,
        message=wizard_message,
    )
    await test_client.send_callback(
        WizardCallback(scope="antiflood_action", op="select", arg="kick_user").pack(),
        from_user=admin,
        message=wizard_message,
    )
    plain_requests = await test_client.send_message(text="ordinary message", from_user=admin, chat=group)
    assert not plain_requests
    await test_client.send_callback(
        WizardCallback(scope="antiflood_action", op="done").pack(),
        from_user=admin,
        message=wizard_message,
    )

    settings = await AntifloodModel.get_by_chat_iid(chat.iid)
    assert settings is not None
    assert [action.name for action in settings.actions] == ["kick_user"]
