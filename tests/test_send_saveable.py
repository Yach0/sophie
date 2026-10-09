from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from aiogram import Bot
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMediaGroup, SendRichMessage, SendVideo, SendVideoNote, SendVoice, TelegramMethod
from aiogram.types import Chat, Message, RichBlockParagraph, RichMessage
from stfu_tg import Bold

from sophie_bot.config import CONFIG
from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes import NoteFile, Saveable
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils import send as send_module
from sophie_bot.modules.notes.utils.media import MEDIA_CAPTION_LENGTH_LIMIT
from sophie_bot.modules.utils_.telegram_exceptions import REPLIED_NOT_FOUND
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
    )

    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_send_saveable_measures_text_after_html_parsing(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    """HTML tags do not count toward Telegram's post-entity-parsing text limit."""
    emitted = _capture_emitted(monkeypatch)
    text = f"<b>{'a' * (TELEGRAM_MESSAGE_LENGTH_LIMIT - 1)}</b>"

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        bot=test_services.bot,
    )

    assert emitted[0].text == text


@pytest.mark.asyncio
async def test_send_saveable_omits_title_when_note_fills_message_limit(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    """Retrieval decoration must not make an otherwise valid saved note unretrievable."""
    emitted = _capture_emitted(monkeypatch)
    text = "a" * TELEGRAM_MESSAGE_LENGTH_LIMIT

    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        title=Bold("Note title"),
        bot=test_services.bot,
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
    )

    assert emitted[0].text == "<b>Note title</b>\nNote text"


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
    )
    assert len(emitted) == 1
    assert (emitted[0].caption if caption else emitted[0].text) == text
    with pytest.raises(SophieException):
        await send_module.send_saveable(
            message=None,
            send_to=-100123,
            saveable=Saveable(text=text + "a", file=note_file, version=2),
            bot=test_services.bot,
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
    )
    assert len(emitted) == 1
    assert emitted[0].media[0].caption == text
    assert emitted[0].media[1].caption is None


@pytest.mark.asyncio
async def test_title_randomness_is_processed_independently_before_assembly(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    emitted = _capture_emitted(monkeypatch)
    choice = Mock(return_value="T")
    monkeypatch.setattr("sophie_bot.modules.notes.utils._random_parser.choice", choice)
    text = "a" * (TELEGRAM_MESSAGE_LENGTH_LIMIT - 2)
    await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text=text, version=2),
        title=Bold("%%%T%%%Long title%%%"),
        bot=test_services.bot,
    )
    choice.assert_called_once_with(["T", "Long title"])
    assert "".join(method.text for method in emitted) == "<b>T</b>\n" + text


@pytest.fixture
async def recipient_send_services(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> AsyncGenerator[ApplicationServices]:
    bot = Bot(token=CONFIG.token)
    monkeypatch.setattr(test_services, "bot", bot)
    yield test_services
    await bot.session.close()


@pytest.fixture(params=["text", "rich", "photo"], ids=str)
def recipient_saveable(request: pytest.FixtureRequest) -> Saveable:
    if request.param == "rich":
        return Saveable(
            text="Private welcome",
            rich_message=RichMessage(blocks=[RichBlockParagraph(text="Private welcome")]),
            version=3,
        )
    if request.param == "text":
        return Saveable(text="Private welcome", version=2)
    return Saveable(
        text="Private welcome",
        file=NoteFile(id="welcome-file", type=ContentType(request.param)),
        version=2,
    )


def _capture_send_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    failure_messages: list[str],
) -> tuple[list[TelegramMethod[Any]], list[TelegramBadRequest]]:
    emitted: list[TelegramMethod[Any]] = []
    errors: list[TelegramBadRequest] = []

    async def make_request(bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Message:
        emitted.append(method)
        if len(emitted) <= len(failure_messages):
            error = TelegramBadRequest(method=method, message=failure_messages[len(emitted) - 1])
            errors.append(error)
            raise error
        return Message(message_id=42, date=0, chat=Chat(id=-100123, type="supergroup"))

    monkeypatch.setattr(test_services.bot.session, "make_request", make_request)
    return emitted, errors


def _assert_ephemeral_recipient(methods: list[TelegramMethod[Any]], receiver_user_id: int) -> None:
    for method in methods:
        payload = method.model_dump(exclude_none=True)
        if isinstance(method, SendRichMessage):
            assert payload["ephemeral_message_parameters"]["receiver_user_id"] == receiver_user_id
        else:
            assert payload["receiver_user_id"] == receiver_user_id


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_reply", [False, True], ids=["initial-send", "missing-reply-retry"])
@pytest.mark.parametrize("error_message", ["USER_NOT_PARTICIPANT", "Bad Request: USER_NOT_PARTICIPANT"])
async def test_send_saveable_skips_unavailable_ephemeral_recipient(
    monkeypatch: pytest.MonkeyPatch,
    recipient_send_services: ApplicationServices,
    recipient_saveable: Saveable,
    missing_reply: bool,
    error_message: str,
) -> None:
    failure_messages = [REPLIED_NOT_FOUND, error_message] if missing_reply else [error_message]
    emitted, errors = _capture_send_outcomes(monkeypatch, recipient_send_services, failure_messages)
    skipped_log = Mock()
    monkeypatch.setattr(send_module.log, "info", skipped_log)
    collected: list[Message] = []

    result = await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=recipient_saveable,
        reply_to=17,
        message_thread_id=99,
        receiver_user_id=42,
        collect_sent=collected,
        bot=recipient_send_services.bot,
    )

    assert result is None
    assert collected == []
    assert len(emitted) == len(failure_messages)
    assert len(errors) == len(failure_messages)
    _assert_ephemeral_recipient(emitted, 42)
    assert emitted[0].model_dump()["reply_parameters"]["message_id"] == 17
    if missing_reply:
        assert emitted[-1].model_dump().get("reply_parameters") is None
    assert all(method.model_dump()["message_thread_id"] == 99 for method in emitted)
    assert any(
        call.kwargs.get("outcome") == "skipped" and call.kwargs.get("reason") == "recipient_unavailable"
        for call in skipped_log.call_args_list
    )


@pytest.mark.asyncio
async def test_send_saveable_missing_reply_retry_keeps_ephemeral_recipient(
    monkeypatch: pytest.MonkeyPatch,
    recipient_send_services: ApplicationServices,
    recipient_saveable: Saveable,
) -> None:
    emitted, _errors = _capture_send_outcomes(monkeypatch, recipient_send_services, [REPLIED_NOT_FOUND])
    collected: list[Message] = []

    result = await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=recipient_saveable,
        reply_to=17,
        receiver_user_id=42,
        collect_sent=collected,
        bot=recipient_send_services.bot,
    )

    assert isinstance(result, Message)
    assert collected == [result]
    assert len(emitted) == 2
    _assert_ephemeral_recipient(emitted, 42)
    assert emitted[0].model_dump()["reply_parameters"]["message_id"] == 17
    assert emitted[1].model_dump().get("reply_parameters") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_reply", [False, True], ids=["initial-send", "missing-reply-retry"])
@pytest.mark.parametrize(
    ("receiver_user_id", "error_message"),
    [
        (None, "USER_NOT_PARTICIPANT"),
        (42, "UNEXPECTED_SEND_ERROR"),
        (42, "USER_NOT_PARTICIPANT_OTHER"),
    ],
    ids=["public-recipient-error", "unrelated-ephemeral-error", "different-recipient-error"],
)
async def test_send_saveable_surfaces_non_recipient_failure(
    monkeypatch: pytest.MonkeyPatch,
    recipient_send_services: ApplicationServices,
    recipient_saveable: Saveable,
    receiver_user_id: int | None,
    error_message: str,
    missing_reply: bool,
) -> None:
    failure_messages = [REPLIED_NOT_FOUND, error_message] if missing_reply else [error_message]
    emitted, errors = _capture_send_outcomes(monkeypatch, recipient_send_services, failure_messages)
    collected: list[Message] = []

    with pytest.raises(TelegramBadRequest) as raised:
        await send_module.send_saveable(
            message=None,
            send_to=-100123,
            saveable=recipient_saveable,
            reply_to=17,
            receiver_user_id=receiver_user_id,
            collect_sent=collected,
            bot=recipient_send_services.bot,
        )

    assert raised.value is errors[-1]
    assert len(emitted) == len(failure_messages)
    assert collected == []
    if receiver_user_id is not None:
        _assert_ephemeral_recipient(emitted, receiver_user_id)
    else:
        assert all(method.model_dump().get("receiver_user_id") is None for method in emitted)
        assert all(method.model_dump().get("ephemeral_message_parameters") is None for method in emitted)


@pytest.mark.asyncio
async def test_send_saveable_album_recipient_error_is_not_ephemeral(
    monkeypatch: pytest.MonkeyPatch,
    recipient_send_services: ApplicationServices,
) -> None:
    emitted, errors = _capture_send_outcomes(monkeypatch, recipient_send_services, ["USER_NOT_PARTICIPANT"])

    with pytest.raises(TelegramBadRequest) as raised:
        await send_module.send_saveable(
            message=None,
            send_to=-100123,
            saveable=Saveable(
                text="Public album",
                files=[NoteFile(id="first", type=ContentType.PHOTO), NoteFile(id="second", type=ContentType.PHOTO)],
                version=2,
            ),
            receiver_user_id=42,
            bot=recipient_send_services.bot,
        )

    assert raised.value is errors[0]
    assert len(emitted) == 1
    assert isinstance(emitted[0], SendMediaGroup)
    assert emitted[0].model_dump().get("receiver_user_id") is None
