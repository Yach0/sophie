"""End-to-end tests for /pin and /unpin.

pin/unpin call bot.pin_chat_message / unpin_chat_message (captured as OTHER requests). The
default pin is silent (disable_notification=True); `loud` flips it.
"""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.types import RequestType

from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id, send_reply_command


async def _moderated_group(test_client: TestClient) -> tuple[object, object, object]:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Pin Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    member = test_client.create_user(user_id=next_user_id(), first_name="Member", username="pin_member")
    await test_client.send_message(text="init", from_user=member.user, chat=group)
    return admin, group, member.user


def _pin_calls(requests: list) -> list:
    return [
        request
        for request in requests
        if request.request_type == RequestType.OTHER and "disable_notification" in request.params
    ]


@pytest.mark.asyncio
async def test_pin_pins_the_replied_message_silently(test_client: TestClient) -> None:
    admin, group, member = await _moderated_group(test_client)
    target = MessageFactory.create(text="pin me", from_user=member, chat=group)

    requests = await send_reply_command(test_client, command="pin", from_user=admin, group=group, replied=target)

    pins = _pin_calls(requests)
    assert pins, "/pin should call pinChatMessage"
    assert pins[0].params["message_id"] == target.message_id
    assert pins[0].params["disable_notification"] is True, "Pins are silent by default"


@pytest.mark.asyncio
async def test_pin_loud_notifies(test_client: TestClient) -> None:
    admin, group, member = await _moderated_group(test_client)
    target = MessageFactory.create(text="pin me loudly", from_user=member, chat=group)

    requests = await send_reply_command(
        test_client, command="pin", from_user=admin, group=group, replied=target, args="loud"
    )

    pins = _pin_calls(requests)
    assert pins and pins[0].params["disable_notification"] is False, "`loud` should notify"


@pytest.mark.asyncio
async def test_pin_needs_a_reply(test_client: TestClient) -> None:
    admin, group, _member = await _moderated_group(test_client)

    requests = await test_client.send_command(command="pin", from_user=admin, chat=group)

    assert any("reply to a message" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_pin_requires_pin_rights(test_client: TestClient) -> None:
    _admin, group, member = await _moderated_group(test_client)
    weak = test_client.create_user(user_id=next_user_id(), first_name="WeakAdmin", username="weak_pinner")
    await test_client.send_message(text="init", from_user=weak.user, chat=group)
    await grant_admin(group.id, weak.user.id, can_pin_messages=False)
    target = MessageFactory.create(text="cannot pin", from_user=member, chat=group)

    requests = await send_reply_command(test_client, command="pin", from_user=weak.user, group=group, replied=target)

    assert not _pin_calls(requests), "An admin without can_pin_messages cannot pin"


@pytest.mark.asyncio
async def test_unpin_replied_message(test_client: TestClient) -> None:
    admin, group, member = await _moderated_group(test_client)
    target = MessageFactory.create(text="unpin me", from_user=member, chat=group)

    requests = await send_reply_command(test_client, command="unpin", from_user=admin, group=group, replied=target)

    unpins = [
        request
        for request in requests
        if request.request_type == RequestType.OTHER and request.params.get("message_id") == target.message_id
    ]
    assert unpins, "/unpin should call unpinChatMessage for the replied message"
