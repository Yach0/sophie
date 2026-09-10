from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendVideo, SendVideoNote, SendVoice
from stfu_tg import Bold

from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes import NoteFile, Saveable
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils import send as send_module
from sophie_bot.modules.notes.utils.media import MEDIA_CAPTION_LENGTH_LIMIT
from sophie_bot.services.application import ApplicationServices
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
async def test_send_saveable_forwards_message_thread_id(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    emitted = _capture_emitted(monkeypatch)

    result = await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text="Threaded note", version=2),
        message_thread_id=987,
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert result is not None
    assert emitted[0].message_thread_id == 987
    assert emitted[0].reply_markup.inline_keyboard == []


@pytest.mark.asyncio
async def test_send_saveable_video_note_uses_send_video_note(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    """Regression: VIDEO_NOTE mapped to SendVideo, which has no `video_note` field.

    Building the method raised a pydantic ValidationError before any HTTP call, so
    `common_try` (TelegramAPIError only) never saw it and the note was unretrievable.
    """
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(
            text="",
            file=NoteFile(id="vn-file-id", type=ContentType.VIDEO_NOTE),
            version=2,
        ),
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert isinstance(emitted[0], SendVideoNote)
    assert emitted[0].video_note == "vn-file-id"


@pytest.mark.asyncio
async def test_send_saveable_video_keeps_caption_and_buttons(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
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
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert isinstance(emitted[0], SendVideo)
    assert emitted[0].video == "video-file-id"
    assert emitted[0].caption == "Video caption"
    assert emitted[0].reply_markup.inline_keyboard[0][0].text == "Button"


@pytest.mark.asyncio
async def test_send_saveable_voice_keeps_caption_and_buttons(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
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
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert isinstance(emitted[0], SendVoice)
    assert emitted[0].caption == "Voice caption"
    assert emitted[0].reply_markup.inline_keyboard[0][0].text == "Button"


@pytest.mark.asyncio
async def test_send_saveable_sticker_keeps_buttons_without_caption(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
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
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert emitted[0].sticker == "sticker-file-id"
    assert emitted[0].reply_markup.inline_keyboard[0][0].text == "Button"
    assert not hasattr(emitted[0], "caption")


@pytest.mark.asyncio
async def test_send_saveable_rejects_over_long_caption(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
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
            bot=test_services.bot,
            redis=test_services.redis,
        )

    assert emitted == []


@pytest.mark.asyncio
async def test_send_saveable_allows_long_text_without_media(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    """The 1024 cap applies to captions only; a plain text note keeps the message limit."""
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(
            text="a" * (MEDIA_CAPTION_LENGTH_LIMIT + 1),
            version=2,
        ),
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert len(emitted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("split_long_text", [False, True])
async def test_send_saveable_measures_text_after_html_parsing(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    split_long_text: bool,
) -> None:
    """HTML tags do not count toward Telegram's post-entity-parsing text limit."""
    emitted = _capture_emitted(monkeypatch)
    text = f"<b>{'a' * (TELEGRAM_MESSAGE_LENGTH_LIMIT - 1)}</b>"

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        split_long_text=split_long_text,
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert emitted[0].text == text


@pytest.mark.asyncio
@pytest.mark.parametrize("split_long_text", [False, True])
async def test_send_saveable_omits_title_when_note_fills_message_limit(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    split_long_text: bool,
) -> None:
    """Retrieval decoration must not make an otherwise valid saved note unretrievable."""
    emitted = _capture_emitted(monkeypatch)
    text = "a" * TELEGRAM_MESSAGE_LENGTH_LIMIT

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        title=Bold("Note title"),
        split_long_text=split_long_text,
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert len(emitted) == 1

    assert emitted[0].text == text


@pytest.mark.asyncio
async def test_send_saveable_keeps_title_when_rendered_text_fits(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    emitted = _capture_emitted(monkeypatch)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text="Note text", version=2),
        title=Bold("Note title"),
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert emitted[0].text == "<b>Note title</b>\nNote text"


@pytest.mark.asyncio
async def test_send_saveable_keeps_title_when_splitting_long_text(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    emitted = _capture_emitted(monkeypatch)
    text = "a" * (TELEGRAM_MESSAGE_LENGTH_LIMIT + 1)

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        title=Bold("Note title"),
        split_long_text=True,
        bot=test_services.bot,
        redis=test_services.redis,
    )

    assert len(emitted) == 2
    assert "".join(method.text for method in emitted) == f"Note title\n{text}"
    assert all(len(method.text) <= TELEGRAM_MESSAGE_LENGTH_LIMIT for method in emitted)
    assert all(method.parse_mode is None for method in emitted)


@pytest.mark.parametrize(
    ("source", "visible"),
    [
        ("&lt;b&gt;&amp;&quot;", '<b>&"'),
        ("&amp;lt;b&amp;gt;", "&lt;b&gt;"),
        ("&#65;&#x41;&#x1F600;", "AA😀"),
        ("&#65!&#x41!&lt!&amp1", "A!A!<!&1"),
        ("&#0000065;&#x000041;", "AA"),
        ("&#00000065;&#x0000041;", "&#00000065;&#x0000041;"),
        ("&#0;&#x0;&#1114111;&#x10FFFF;&#1114112;", "&#0;&#x0;&#1114111;&#x10FFFF;&#1114112;"),
        ("&#1114110;&#x10FFFE;", "\U0010fffe\U0010fffe"),
        ("&#128;&#1;&#xFFFF;", "\x80\x01\uffff"),
        ("&#X41;&#-1;&#x;", "&#X41;&#-1;&#x;"),
        ("&apos;&nbsp;&copy;&NotEqualTilde;&AMP;&unknown;", "&apos;&nbsp;&copy;&NotEqualTilde;&AMP;&unknown;"),
        ('<b><a href="https://example.com/?a=1&amp;b=2"><tg-spoiler>😀&amp;</tg-spoiler></a></b>', "😀&"),
        ('<span class="tg-spoiler"><tg-emoji emoji-id="123">😀</tg-emoji></span>', "😀"),
    ],
)
def test_telegram_text_decoding(source: str, visible: str) -> None:
    assert send_module._telegram_plain_text(source) == visible
    assert send_module._telegram_text_length(source) == len(visible)


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["&amp;", "&amp;lt;", "&#65;", "&#x1F600;", "&nbsp;", "&apos;", "&#X41;"])
@pytest.mark.parametrize("caption", [False, True])
async def test_send_saveable_reference_at_limit(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    reference: str,
    caption: bool,
) -> None:
    emitted = _capture_emitted(monkeypatch)
    visible_lengths = {"&amp;": 1, "&amp;lt;": 4, "&#65;": 1, "&#x1F600;": 1, "&nbsp;": 6, "&apos;": 6, "&#X41;": 6}
    limit = MEDIA_CAPTION_LENGTH_LIMIT if caption else TELEGRAM_MESSAGE_LENGTH_LIMIT
    text = "a" * (limit - visible_lengths[reference]) + reference
    note_file = NoteFile(id="photo", type=ContentType.PHOTO) if caption else None
    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, file=note_file, version=2),
        title=Bold("Title"),
        bot=test_services.bot,
        redis=test_services.redis,
    )
    assert len(emitted) == 1
    assert (emitted[0].caption if caption else emitted[0].text) == text
    with pytest.raises(SophieException):
        await send_module.send_saveable(
            message=None,
            send_to=-100123,
            saveable=Saveable(text=text + "a", file=note_file, version=2),
            bot=test_services.bot,
            redis=test_services.redis,
        )
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_album_uses_rendered_caption_length(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    emitted = _capture_emitted(monkeypatch)
    text = "<b>" + "&amp;" * MEDIA_CAPTION_LENGTH_LIMIT + "</b>"
    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(
            text=text,
            files=[NoteFile(id="first", type=ContentType.PHOTO), NoteFile(id="second", type=ContentType.PHOTO)],
            version=2,
        ),
        bot=test_services.bot,
        redis=test_services.redis,
    )
    assert len(emitted) == 1
    assert emitted[0].media[0].caption == text
    assert emitted[0].media[1].caption is None


@pytest.mark.asyncio
async def test_split_literal_html_retry_disables_parse_mode(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    emitted: list[Any] = []

    async def fake_emit(self: Any, bot: object) -> object:
        emitted.append(self)
        if len(emitted) == 1:
            raise TelegramBadRequest(method=self, message="Bad Request: message to be replied not found")
        return SimpleNamespace(message_id=42)

    monkeypatch.setattr("aiogram.methods.base.TelegramMethod.emit", fake_emit)
    source = "&lt;b&gt;&amp;lt;&nbsp;" + "a" * TELEGRAM_MESSAGE_LENGTH_LIMIT
    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=source, version=2),
        reply_to=123,
        split_long_text=True,
        bot=test_services.bot,
        redis=test_services.redis,
    )
    assert len(emitted) == 3
    assert emitted[0].text == emitted[1].text
    assert emitted[0].reply_parameters.message_id == 123
    assert emitted[1].reply_parameters is None
    assert "".join(method.text for method in emitted[1:]) == "<b>&lt;&nbsp;" + "a" * TELEGRAM_MESSAGE_LENGTH_LIMIT
    assert all(method.parse_mode is None for method in emitted)


@pytest.mark.asyncio
@pytest.mark.parametrize("split", [False, True])
async def test_title_randomness_is_processed_independently_before_assembly(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    split: bool,
) -> None:
    emitted = _capture_emitted(monkeypatch)
    choice = Mock(return_value="T")
    monkeypatch.setattr("sophie_bot.modules.notes.utils._random_parser.choice", choice)
    text = "a" * (TELEGRAM_MESSAGE_LENGTH_LIMIT + 1 if split else TELEGRAM_MESSAGE_LENGTH_LIMIT - 2)
    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        title=Bold("%%%T%%%Long title%%%"),
        split_long_text=split,
        bot=test_services.bot,
        redis=test_services.redis,
    )
    choice.assert_called_once_with(["T", "Long title"])
    assert "".join(method.text for method in emitted) == ("T\n" if split else "<b>T</b>\n") + text
