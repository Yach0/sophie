"""End-to-end tests for /cleannotes — automatic notes cleanup.

Mirrors /cleanwelcome: an admin toggles the setting, and once enabled the bot removes the
note it sent previously, plus the request message itself when that message is nothing but a
note request (`/get name` or `#name`). A request embedded in a longer message is left alone.
"""

from __future__ import annotations

import pytest
from aiogram.types import Chat, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UpdateFactory
from aiogram_test_framework.types import CapturedRequest, RequestType

from sophie_bot.db.models import ChatModel, NoteModel
from sophie_bot.db.models.clean_notes import CleanNotesModel
from tests.e2e.helpers import (
    create_test_user_and_group,
    grant_admin,
    grant_bot_admin,
    next_message_id,
    set_feature,
)


async def _clean_notes(chat_tid: int) -> CleanNotesModel:
    chat = await ChatModel.get_by_tid(chat_tid)
    assert chat is not None
    return await CleanNotesModel.get_by_chat_iid(chat.iid)


def _sends(requests: list[CapturedRequest]) -> list[CapturedRequest]:
    return [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]


def _deleted_ids(requests: list[CapturedRequest]) -> list[int]:
    """Message ids the bot asked Telegram to delete (deleteMessage or deleteMessages)."""
    ids: list[int] = []
    for request in requests:
        ids.extend(request.params.get("message_ids", []))
        if "message_id" in request.params and request.request_type == RequestType.DELETE_MESSAGE:
            ids.append(request.params["message_id"])
    return ids


async def _send_text(
    test_client: TestClient,
    text: str,
    from_user: User,
    chat: Chat,
    message_id: int,
) -> list[CapturedRequest]:
    """Feed a message with a known message id, so deletions can be asserted against it."""
    message = MessageFactory.create(text=text, from_user=from_user, chat=chat, message_id=message_id)
    update = UpdateFactory.create_message_update(message)

    start = len(test_client.capture)
    await test_client.dispatcher.feed_update(bot=test_client.bot, update=update)
    return test_client.capture.all_requests[start:]


async def _group_with_note(test_client: TestClient, *, enabled: bool = True, flag: bool = True) -> tuple[User, Chat]:
    admin, group, _user_model = await create_test_user_and_group(test_client, group_title="CleanNotes Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await NoteModel(chat_id=chat.tid, chat=chat, names=("rules",), text="Be nice", version=2).insert()

    await set_feature("cleannotes", flag)
    if enabled:
        await (await _clean_notes(group.id)).set_status(True)

    return admin, group


# ---------------------------------------------------------------------------
# /cleannotes — admin-only toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleannotes_shows_status(test_client: TestClient) -> None:
    """An admin calling /cleannotes without args sees the current status."""

    admin, group, _user_model = await create_test_user_and_group(test_client, group_title="CleanNotes Status")
    await grant_admin(group.id, admin.id)
    await set_feature("cleannotes", True)

    requests = await test_client.send_command(command="cleannotes", from_user=admin, chat=group)

    assert requests, "Bot should respond to /cleannotes from an admin"
    response_text = requests[-1].text or ""
    assert "Current state" in response_text and "Disabled" in response_text, (
        f"Response should show the current status, got: {response_text}"
    )


@pytest.mark.asyncio
async def test_cleannotes_on_persists(test_client: TestClient) -> None:
    """`/cleannotes on` enables the cleanup in the database."""

    admin, group, _user_model = await create_test_user_and_group(test_client, group_title="CleanNotes Toggle")
    await grant_admin(group.id, admin.id)
    await set_feature("cleannotes", True)

    await test_client.send_command(command="cleannotes", from_user=admin, args="on", chat=group)

    assert (await _clean_notes(group.id)).enabled is True


@pytest.mark.asyncio
async def test_cleannotes_requires_admin(test_client: TestClient) -> None:
    """A regular member cannot toggle the cleanup."""

    member, group, _user_model = await create_test_user_and_group(test_client, group_title="CleanNotes NoAdmin")
    await set_feature("cleannotes", True)

    requests = await test_client.send_command(command="cleannotes", from_user=member, args="on", chat=group)

    assert any("administrator" in (request.text or "").lower() for request in requests), (
        "A non-admin should be told they lack rights"
    )
    assert (await _clean_notes(group.id)).enabled is False


# ---------------------------------------------------------------------------
# Cleanup behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleannotes_deletes_standalone_get_command(test_client: TestClient) -> None:
    """A message that is only `/get name` is removed, and the sent note is tracked."""

    admin, group = await _group_with_note(test_client)

    request_id = next_message_id()
    requests = await _send_text(test_client, "/get rules", admin, group, request_id)

    sent = _sends(requests)
    assert sent, "The note should be sent"
    assert request_id in _deleted_ids(requests), "The standalone /get request should be deleted"
    assert (await _clean_notes(group.id)).last_msgs == [sent[-1].response.message_id], (
        "The id of the note just sent should be recorded for the next cleanup"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "/get rules",
        "/get   rules",
        "/get #rules",
        "/get@test_bot rules",
        "/get rules noformat",
    ],
)
async def test_cleannotes_deletes_standalone_get_command_variants(test_client: TestClient, text: str) -> None:
    """Whitespace, a leading `#`, a bot mention or the raw modifier still make it a bare request."""

    admin, group = await _group_with_note(test_client)

    request_id = next_message_id()
    requests = await _send_text(test_client, text, admin, group, request_id)

    assert _sends(requests), f"The note should be sent for {text!r}"
    assert request_id in _deleted_ids(requests), f"{text!r} should be deleted as a standalone request"


@pytest.mark.asyncio
async def test_cleannotes_keeps_get_command_with_extra_text(test_client: TestClient) -> None:
    """`/get name` followed by other words is a real message and stays."""

    admin, group = await _group_with_note(test_client)

    request_id = next_message_id()
    requests = await _send_text(test_client, "/get rules and also hello", admin, group, request_id)

    assert _sends(requests), "The note should still be sent"
    assert request_id not in _deleted_ids(requests), "A /get message with extra text must not be deleted"


@pytest.mark.asyncio
async def test_cleannotes_deletes_standalone_hashtag_request(test_client: TestClient) -> None:
    """A message that is only `#name` is removed together with the previous note."""

    admin, group = await _group_with_note(test_client)

    first_id = next_message_id()
    first = await _send_text(test_client, "#rules", admin, group, first_id)
    first_note_id = _sends(first)[-1].response.message_id
    assert first_id in _deleted_ids(first), "The standalone hashtag request should be deleted"

    second_id = next_message_id()
    second = await _send_text(test_client, "#rules", admin, group, second_id)
    deleted = _deleted_ids(second)

    assert first_note_id in deleted, "The previously sent note should be deleted"
    assert second_id in deleted, "The new standalone request should be deleted too"


@pytest.mark.asyncio
async def test_cleannotes_deletes_padded_hashtag_request(test_client: TestClient) -> None:
    """Surrounding whitespace does not make `#name` part of a conversation."""

    admin, group = await _group_with_note(test_client)

    request_id = next_message_id()
    requests = await _send_text(test_client, "  #rules \n", admin, group, request_id)

    assert _sends(requests), "The note should be sent"
    assert request_id in _deleted_ids(requests), "A whitespace-padded hashtag request should be deleted"


@pytest.mark.asyncio
async def test_cleannotes_keeps_request_with_extra_text(test_client: TestClient) -> None:
    """`#name` inside a longer message is a real message: it stays, only the old note goes."""

    admin, group = await _group_with_note(test_client)

    first_id = next_message_id()
    first = await _send_text(test_client, "#rules", admin, group, first_id)
    first_note_id = _sends(first)[-1].response.message_id

    second_id = next_message_id()
    second = await _send_text(test_client, "hey everyone, please read #rules", admin, group, second_id)
    deleted = _deleted_ids(second)

    assert _sends(second), "The note should still be sent for an embedded hashtag"
    assert second_id not in deleted, "A message with extra text must not be deleted"
    assert first_note_id in deleted, "The previously sent note should still be cleaned up"


@pytest.mark.asyncio
async def test_cleannotes_disabled_deletes_nothing(test_client: TestClient) -> None:
    """With the cleanup off, nothing is deleted and no message is tracked."""

    admin, group = await _group_with_note(test_client, enabled=False)

    first_id = next_message_id()
    await _send_text(test_client, "#rules", admin, group, first_id)
    second = await _send_text(test_client, "#rules", admin, group, next_message_id())

    assert not _deleted_ids(second), "Nothing should be deleted while /cleannotes is off"
    assert (await _clean_notes(group.id)).last_msgs == []


@pytest.mark.asyncio
async def test_cleannotes_command_is_gated_by_the_feature_flag(test_client: TestClient) -> None:
    """With the flag off the command does not exist, even for an admin."""

    admin, group, _user_model = await create_test_user_and_group(test_client, group_title="CleanNotes Flag Off")
    await grant_admin(group.id, admin.id)
    await set_feature("cleannotes", False)

    requests = await test_client.send_command(command="cleannotes", from_user=admin, chat=group)

    assert not requests, "No handler should answer /cleannotes while the flag is off"


@pytest.mark.asyncio
async def test_cleannotes_cleanup_is_gated_by_the_feature_flag(test_client: TestClient) -> None:
    """An already-enabled chat stops being cleaned when the flag is turned off."""

    admin, group = await _group_with_note(test_client, flag=False)

    await _send_text(test_client, "#rules", admin, group, next_message_id())
    second = await _send_text(test_client, "#rules", admin, group, next_message_id())

    assert not _deleted_ids(second), "Nothing should be deleted while the flag is off"
