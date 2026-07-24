"""End-to-end tests for media-group (album) notes.

Covers the three moving parts of the feature:
- the aggregator middleware buffering an album's updates into one `album` list,
- `parse_saveable` collecting a file from every album item into `Saveable.files`,
- `send_saveable` emitting an album via `sendMediaGroup` (+ buttons follow-up).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.enums import ContentType
from aiogram.types import Chat, Message, PhotoSize, Update, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UpdateFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes import NoteFile, Saveable
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.middlewares.media_group import MediaGroupAggregatorMiddleware, MemoryMediaGroupAggregator
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.modules.notes.utils.send import send_saveable
from tests.e2e.helpers import grant_admin

CHAT_ID = -1002900000001
USER_ID = 929000001


def _photo_message(message_id: int, media_group_id: str, caption: str | None = None) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=CHAT_ID, type="supergroup", title="Album Group"),
        from_user=User(id=USER_ID, is_bot=False, first_name="AlbumUser"),
        media_group_id=media_group_id,
        caption=caption,
        photo=[
            PhotoSize(
                file_id=f"photo-file-{message_id}",
                file_unique_id=f"uniq-{message_id}",
                width=100,
                height=100,
            )
        ],
    )


def _photo_update(update_id: int, message: Message) -> Update:
    return Update(update_id=update_id, message=message)


# ---------------------------------------------------------------------------
# Middleware aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_aggregates_album_into_single_handler_call(test_client: TestClient) -> None:
    """The three updates of one album trigger the handler once with the full album."""
    middleware = MediaGroupAggregatorMiddleware(MemoryMediaGroupAggregator(), delay=0.05)

    albums_seen: list[list[Message] | None] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        albums_seen.append(data.get("album"))
        return "handled"

    updates = [_photo_update(index, _photo_message(index, media_group_id="album-A")) for index in range(1, 4)]

    with patch("sophie_bot.middlewares.media_group.is_enabled", AsyncMock(return_value=True)):
        await asyncio.gather(*(middleware(handler, update, {"bot": test_client.bot}) for update in updates))

    non_empty_albums = [album for album in albums_seen if album]
    assert len(non_empty_albums) == 1, f"Handler should fire once with the album, got {albums_seen}"
    assert len(non_empty_albums[0]) == 3, "The album should contain all three photos"


@pytest.mark.asyncio
async def test_middleware_passes_through_when_flag_disabled(test_client: TestClient) -> None:
    """With the flag off, each album item is handled individually (no aggregation)."""
    middleware = MediaGroupAggregatorMiddleware(MemoryMediaGroupAggregator(), delay=0.05)

    call_count = 0

    async def handler(event: Any, data: dict[str, Any]) -> str:
        nonlocal call_count
        call_count += 1
        assert data.get("album") is None
        return "handled"

    updates = [_photo_update(index, _photo_message(index, media_group_id="album-B")) for index in range(1, 4)]

    with patch("sophie_bot.middlewares.media_group.is_enabled", AsyncMock(return_value=False)):
        await asyncio.gather(*(middleware(handler, update, {"bot": test_client.bot}) for update in updates))

    assert call_count == 3, "Every item should reach the handler when the feature is disabled"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_saveable_collects_album_files() -> None:
    """parse_saveable stores a file per album item in `files` and leaves `file` unset."""
    album = [
        _photo_message(1, media_group_id="album-C", caption="save album Hello"),
        _photo_message(2, media_group_id="album-C"),
        _photo_message(3, media_group_id="album-C"),
    ]

    saveable = await parse_saveable(album[0], text="Hello", buttons=ButtonsList(), album=album)

    assert saveable.file is None, "Album notes must not set the single `file`"
    assert [note_file.id for note_file in saveable.files] == [
        "photo-file-1",
        "photo-file-2",
        "photo-file-3",
    ]
    assert all(note_file.type == ContentType.PHOTO for note_file in saveable.files)


# ---------------------------------------------------------------------------
# Saving by reply to an album
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_reply_to_album_saves_first_and_warns(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replying to an album with /save stores the single replied item and warns the user."""

    group_chat = ChatFactory.create_group(chat_id=CHAT_ID, title="Album Reply Group")
    user_wrapper = test_client.create_user(user_id=USER_ID, first_name="AdminReply", username="admin_reply")
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, user_wrapper.user.id)

    replied_photo = _photo_message(500, media_group_id="reply-album")
    command_message = MessageFactory.create(
        text="/save album",
        from_user=user_wrapper.user,
        chat=group_chat,
        reply_to_message=replied_photo,
    )
    update = UpdateFactory.create_message_update(command_message)

    with (
        patch("sophie_bot.modules.logging.utils.log.log_event", AsyncMock()),
    ):
        start = len(test_client.capture)
        await test_client.dispatcher.feed_update(bot=test_client.bot, update=update)
        requests = test_client.capture.all_requests[start:]

    response_text = next((request.text for request in reversed(requests) if request.text), "")
    assert "Note was successfully" in response_text, f"Note should be saved, got: {response_text}"
    assert "Only the first media of the album was saved" in response_text, (
        f"Response should warn about partial album save, got: {response_text}"
    )
    assert "/save album" in response_text, "Warning should show the caption-based command"


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _album_saveable(text: str = "", buttons: list[list[Any]] | None = None) -> Saveable:
    return Saveable(
        text=text,
        file=None,
        files=[
            NoteFile(id="photo-file-1", type=ContentType.PHOTO),
            NoteFile(id="photo-file-2", type=ContentType.PHOTO),
            NoteFile(id="photo-file-3", type=ContentType.PHOTO),
        ],
        buttons=buttons or [],
    )


@pytest.mark.asyncio
async def test_send_saveable_sends_media_group(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """An album note is sent as a single sendMediaGroup with the caption on the first item."""

    await send_saveable(message=None, send_to=CHAT_ID, saveable=_album_saveable(text="Album caption"))

    media_group_requests = test_client.capture.get_by_type(RequestType.SEND_MEDIA_GROUP)
    assert len(media_group_requests) == 1, "Exactly one sendMediaGroup should be emitted"

    # params are `method.model_dump(exclude_none=True)`, so media items are dicts and a
    # None caption is simply absent from the dict.
    media = media_group_requests[-1].params["media"]
    assert len(media) == 3, "All three photos should be in the album"
    assert media[0]["caption"] == "Album caption", "Caption belongs on the first item"
    assert all("caption" not in item for item in media[1:]), "Only the first item carries the caption"


@pytest.mark.asyncio
async def test_send_saveable_single_photo_note(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single-media (photo) note sends a sendPhoto with the file id and caption set."""

    saveable = Saveable(text="A caption", file=NoteFile(id="single-photo", type=ContentType.PHOTO))

    await send_saveable(message=None, send_to=CHAT_ID, saveable=saveable)

    photo_requests = test_client.capture.get_by_type(RequestType.SEND_PHOTO)
    assert len(photo_requests) == 1, "A single photo note should emit exactly one sendPhoto"
    params = photo_requests[-1].params
    assert params["photo"] == "single-photo", "The photo file id must be sent"
    assert params["caption"] == "A caption", "The caption must be set"


@pytest.mark.asyncio
async def test_send_saveable_album_buttons_go_to_followup(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Album notes with buttons emit the album, then a follow-up message carrying the buttons."""

    saveable = _album_saveable(
        text="With buttons",
        buttons=[[Button(text="Docs", action=ButtonAction.url, data="https://example.com")]],
    )

    await send_saveable(message=None, send_to=CHAT_ID, saveable=saveable)

    assert len(test_client.capture.get_by_type(RequestType.SEND_MEDIA_GROUP)) == 1, "Album should still be sent"

    followups = test_client.capture.get_by_type(RequestType.SEND_MESSAGE)
    assert followups, "A follow-up message should carry the buttons"
    assert followups[-1].reply_markup is not None, "The follow-up must include the inline keyboard"
