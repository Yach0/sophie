from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.enums import ContentType
from aiogram.methods import SendVideo, SendVideoNote, SendVoice
from stfu_tg import Bold

from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes import NoteFile, Saveable
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils import send as send_module
from sophie_bot.modules.notes.utils.media import MEDIA_CAPTION_LENGTH_LIMIT
from sophie_bot.utils.exception import SophieException


def _capture_emitted(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Captures the built send methods instead of emitting them to Telegram.

    Patches `TelegramMethod.emit` so the real aiogram method classes (and their pydantic
    validation) still run — a mock send method would not catch a field mismatch.
    """
    emitted: list[Any] = []

    def fake_emit(self: Any, bot: object) -> Any:
        emitted.append(self)

        async def emit_result() -> object:
            return SimpleNamespace(message_id=42)

        return emit_result()

    monkeypatch.setattr("aiogram.methods.base.TelegramMethod.emit", fake_emit)
    return emitted


def _url_button(text: str, url: str) -> Button:
    return Button(text=text, action=ButtonAction.url, data=url)


@pytest.mark.asyncio
async def test_send_saveable_forwards_message_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted = _capture_emitted(monkeypatch)

    result = await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text="Threaded note", version=2),
        message_thread_id=987,
    )

    assert result is not None
    assert emitted[0].message_thread_id == 987
    assert emitted[0].reply_markup.inline_keyboard == []


@pytest.mark.asyncio
async def test_send_saveable_video_note_uses_send_video_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: VIDEO_NOTE mapped to SendVideo, which has no `video_note` field.

    Building the method raised a pydantic ValidationError before any HTTP call, so
    `common_try` (TelegramAPIError only) never saw it and the note was unretrievable.
    """
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text="", file=NoteFile(id="vn-file-id", type=ContentType.VIDEO_NOTE), version=2),
    )

    assert isinstance(emitted[0], SendVideoNote)
    assert emitted[0].video_note == "vn-file-id"


@pytest.mark.asyncio
async def test_send_saveable_video_keeps_caption_and_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: VIDEO was absent from SUPPORTS_CAPTION, so text and buttons were dropped."""
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(
            text="Video caption",
            file=NoteFile(id="video-file-id", type=ContentType.VIDEO),
            buttons=[[_url_button("Button", "https://example.com")]],
            version=2,
        ),
    )

    assert isinstance(emitted[0], SendVideo)
    assert emitted[0].video == "video-file-id"
    assert emitted[0].caption == "Video caption"
    assert emitted[0].reply_markup.inline_keyboard[0][0].text == "Button"


@pytest.mark.asyncio
async def test_send_saveable_voice_keeps_caption_and_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: VOICE was absent from SUPPORTS_CAPTION, so text and buttons were dropped."""
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(
            text="Voice caption",
            file=NoteFile(id="voice-file-id", type=ContentType.VOICE),
            buttons=[[_url_button("Button", "https://example.com")]],
            version=2,
        ),
    )

    assert isinstance(emitted[0], SendVoice)
    assert emitted[0].caption == "Voice caption"
    assert emitted[0].reply_markup.inline_keyboard[0][0].text == "Button"


@pytest.mark.asyncio
async def test_send_saveable_sticker_keeps_buttons_without_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: reply_markup was gated on caption support, but sendSticker takes buttons."""
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(
            text="",
            file=NoteFile(id="sticker-file-id", type=ContentType.STICKER),
            buttons=[[_url_button("Button", "https://example.com")]],
            version=2,
        ),
    )

    assert emitted[0].sticker == "sticker-file-id"
    assert emitted[0].reply_markup.inline_keyboard[0][0].text == "Button"
    assert not hasattr(emitted[0], "caption")


@pytest.mark.asyncio
async def test_send_saveable_rejects_over_long_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the guard used the 4090 text limit, but a caption caps at 1024.

    Telegram answered MEDIA_CAPTION_TOO_LONG, which `common_try` re-raises, so every
    retrieval of the note crashed unhandled instead of surfacing a user-facing error.
    """
    emitted = _capture_emitted(monkeypatch)

    with pytest.raises(SophieException):
        await send_module.send_saveable(
            message=None,
            send_to=-100123,
            saveable=Saveable(
                text="a" * (MEDIA_CAPTION_LENGTH_LIMIT + 1),
                file=NoteFile(id="photo-file-id", type=ContentType.PHOTO),
                version=2,
            ),
        )

    assert emitted == []


@pytest.mark.asyncio
async def test_send_saveable_allows_long_text_without_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 1024 cap applies to captions only; a plain text note keeps the message limit."""
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text="a" * (MEDIA_CAPTION_LENGTH_LIMIT + 1), version=2),
    )

    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_send_saveable_measures_text_after_html_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTML tags do not count toward Telegram's post-entity-parsing text limit."""
    emitted = _capture_emitted(monkeypatch)
    text = f"<b>{'a' * (TELEGRAM_MESSAGE_LENGTH_LIMIT - 1)}</b>"

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
    )

    assert emitted[0].text == text


@pytest.mark.asyncio
async def test_send_saveable_omits_title_when_note_fills_message_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrieval decoration must not make an otherwise valid saved note unretrievable."""
    emitted = _capture_emitted(monkeypatch)
    text = "a" * TELEGRAM_MESSAGE_LENGTH_LIMIT

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        title=Bold("Note title"),
    )

    assert emitted[0].text == text


@pytest.mark.asyncio
async def test_send_saveable_keeps_title_when_rendered_text_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text="Note text", version=2),
        title=Bold("Note title"),
    )

    assert emitted[0].text == "<b>Note title</b>\nNote text"
