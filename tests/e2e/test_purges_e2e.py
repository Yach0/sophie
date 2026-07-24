"""End-to-end tests for the purges module: /del and /purge.

Both reply to a message and delete a range via bot.delete_messages (captured as an OTHER
request carrying `message_ids`). /purge sleeps before self-deleting its status message, so the
sleep is stubbed to keep the test fast.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.types import Message, Update, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory

from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_message_id, next_user_id


async def _moderated_group(test_client: TestClient) -> tuple[User, object, User]:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Purge Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    member = test_client.create_user(user_id=next_user_id(), first_name="Member", username="purge_member")
    await test_client.send_message(text="init", from_user=member.user, chat=group)
    return admin, group, member.user


async def _send_reply_command(
    test_client: TestClient, *, command: str, from_user: User, group: object, replied: Message
) -> list:
    """Feed a `/command` that replies to `replied`, returning the captured requests."""
    message = MessageFactory.create(text=f"/{command}", from_user=from_user, chat=group, reply_to_message=replied)
    start = len(test_client.capture)
    await test_client.dispatcher.feed_update(
        bot=test_client.bot, update=Update(update_id=next_message_id(), message=message)
    )
    return test_client.capture.all_requests[start:]


def _deleted_ids(requests: list) -> list[int]:
    ids: list[int] = []
    for request in requests:
        ids.extend(request.params.get("message_ids", []))
    return ids


@pytest.mark.asyncio
async def test_del_removes_the_replied_message(test_client: TestClient) -> None:
    admin, group, member = await _moderated_group(test_client)
    replied = MessageFactory.create(text="spam to remove", from_user=member, chat=group)

    requests = await _send_reply_command(test_client, command="del", from_user=admin, group=group, replied=replied)

    assert replied.message_id in _deleted_ids(requests), "The replied message should be deleted by /del"


@pytest.mark.asyncio
async def test_del_without_reply_asks_for_one(test_client: TestClient) -> None:
    admin, group, _member = await _moderated_group(test_client)

    requests = await test_client.send_command(command="del", from_user=admin, chat=group)

    assert any("reply to a message" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_del_requires_admin(test_client: TestClient) -> None:
    _admin, group, member = await _moderated_group(test_client)
    replied = MessageFactory.create(text="earlier message", from_user=member, chat=group)

    requests = await _send_reply_command(test_client, command="del", from_user=member, group=group, replied=replied)

    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert replied.message_id not in _deleted_ids(requests)


@pytest.mark.asyncio
async def test_purge_removes_the_range(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, group, member = await _moderated_group(test_client)

    # The status message self-deletes after a sleep; skip the wait.
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.sleep", _no_sleep)

    # The replied message must be older (smaller id) than the /purge command that follows it;
    # MessageFactory's auto-incrementing id guarantees that ordering.
    replied = MessageFactory.create(
        text="start of purge", from_user=member, chat=group, date=datetime.now(UTC)
    )

    requests = await _send_reply_command(test_client, command="purge", from_user=admin, group=group, replied=replied)

    deleted = _deleted_ids(requests)
    assert replied.message_id in deleted, "The purge should delete from the replied message onward"
    completed = " ".join(request.text or "" for request in requests)
    assert "Purge completed" in completed


@pytest.mark.asyncio
async def test_purge_without_reply_asks_for_one(test_client: TestClient) -> None:
    admin, group, _member = await _moderated_group(test_client)

    requests = await test_client.send_command(command="purge", from_user=admin, chat=group)

    assert any("reply to a message" in (request.text or "").lower() for request in requests)
