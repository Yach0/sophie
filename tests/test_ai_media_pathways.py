from __future__ import annotations

import asyncio
import hashlib
import resource
import runpy
import sys
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import av
import httpx
import httpx2
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile
from aiogram.types import Audio, Message, Voice
from aiohttp import ClientConnectionError
from beanie import PydanticObjectId
from mistralai.client.errors import NoResponseError, SDKError
from PIL import Image
from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart
from pydantic_ai.models.test import TestModel

from sophie_bot.constants import AI_MAX_VIDEO_SIZE_BYTES
from sophie_bot.modules.ai.middlewares.cache_user_messages import CacheUserMessagesMiddleware
from sophie_bot.modules.ai.utils import animation_still, media_download, transform_animation, transform_audio
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate
from sophie_bot.modules.ai.utils.ai_tokens import ModelTokenBudget
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import (
    CachedMedia,
    MessageType,
    cache_message,
    get_cached_messages,
    get_context_epoch_key,
    reset_modern_context,
)
from sophie_bot.modules.ai.utils.modern_context import build_message_parts
from sophie_bot.modules.ai.utils.old_context import OldContext
from sophie_bot.services.application import ApplicationServices
from tests.test_modern_context import CHAT_TID, OTHER_TID, _build, _byte_counter, _events, _message
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401


@pytest.fixture
def context(test_services: ApplicationServices) -> SophieAIToolContext:
    return SophieAIToolContext(
        connection=MagicMock(), chat_tid=CHAT_TID, chat_iid=PydanticObjectId(), services=test_services
    )


def _media_message(kind: str, *, thumbnail: bool = False) -> Message:
    media = {"file_id": "attachment", "file_unique_id": "unique", "file_size": 200}
    if kind in {"voice", "audio", "animation", "video", "video_note"}:
        media["duration"] = 2
    if kind == "video":
        media.update(width=16, height=16)
    if kind == "video_note":
        media["length"] = 16
    if kind == "animation":
        media.update(width=16, height=16, mime_type="video/mp4", file_name="clip.mp4")
    if kind == "audio":
        media.update(mime_type="audio/mp4", file_name="recording.m4a")
    if kind == "document":
        media.update(mime_type="image/gif", file_name="clip.gif")
    if thumbnail:
        media["thumbnail"] = {"file_id": "thumb", "file_unique_id": "thumb", "width": 16, "height": 16}
    return _message(1).model_copy(
        update={
            "text": None,
            kind: Message.model_validate(
                {
                    "message_id": 1,
                    "date": datetime.now(UTC),
                    "chat": {"id": CHAT_TID, "type": "supergroup"},
                    kind: media,
                }
            ).__getattribute__(kind),
        }
    )


def _gif() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format="GIF", duration=1000)
    return buffer.getvalue()


def _download(bot: Bot, data: bytes, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    async def download(file_id: str, *, destination: BytesIO) -> BytesIO:
        destination.write(data)
        return destination

    mocked = AsyncMock(side_effect=download)
    monkeypatch.setattr(bot, "download", mocked)
    return mocked


@pytest.mark.parametrize("kind", ["voice", "audio"])
async def test_audio_uses_shared_transcription_with_original_metadata(
    kind: str, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = _media_message(kind)
    download = _download(test_services.bot, b"audio", monkeypatch)
    complete = AsyncMock(return_value=SimpleNamespace(text="spoken words\n"))
    monkeypatch.setattr(
        transform_audio,
        "get_mistral_client",
        AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=complete)))
        ),
    )
    parts = await build_message_parts(
        message, "caption", "", None, True, bot=test_services.bot, redis=test_services.redis
    )
    assert parts == ["caption", "spoken words"]
    download.assert_awaited_once()
    payload = complete.await_args.kwargs["file"]
    assert payload["file_name"] == ("recording.m4a" if kind == "audio" else "audio.ogg")
    assert payload["content_type"] == ("audio/mp4" if kind == "audio" else "audio/ogg")
    assert payload["content"].read() == b"audio"


@pytest.mark.parametrize("kind", ["voice", "audio"])
@pytest.mark.parametrize("bound", ["duration", "file_size"])
async def test_audio_bounds_reject_before_download(
    kind: str, bound: str, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = _media_message(kind)
    media: Audio | Voice = message.audio or message.voice
    media = media.model_copy(
        update={bound: transform_audio.MAX_AUDIO_DURATION + 1 if bound == "duration" else AI_MAX_VIDEO_SIZE_BYTES + 1}
    )
    download = _download(test_services.bot, b"audio", monkeypatch)
    with pytest.raises(media_download.MediaLimitError):
        await transform_audio.transform_voice_to_text(media, bot=test_services.bot, redis=test_services.redis)
    download.assert_not_awaited()


@pytest.mark.parametrize("kind", ["animation", "document"])
async def test_missing_thumbnail_decodes_provider_image_and_cleans_temp_files(
    kind: str, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    _download(test_services.bot, _gif(), monkeypatch)
    created_paths: list[Path] = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        created_paths.append(Path(args[-2]))
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(transform_animation.asyncio, "create_subprocess_exec", spawn)
    parts = await asyncio.wait_for(
        build_message_parts(
            _media_message(kind), "caption", "", None, True, bot=test_services.bot, redis=test_services.redis
        ),
        timeout=10,
    )
    assert parts[0] == "caption"
    images = [part for part in parts if isinstance(part, BinaryContent)]
    assert images[0].media_type == "image/jpeg"
    with Image.open(BytesIO(images[0].data)) as image:
        assert image.size == (16, 16)
    assert created_paths and all(not path.exists() for path in created_paths)


@pytest.mark.parametrize("kind", ["animation"])
async def test_thumbnail_path_is_preserved(
    kind: str, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    download = _download(test_services.bot, b"thumbnail", monkeypatch)
    parts = await build_message_parts(
        _media_message(kind, thumbnail=True),
        "caption",
        "",
        None,
        True,
        bot=test_services.bot,
        redis=test_services.redis,
    )
    assert parts[2] == BinaryContent(data=b"thumbnail", media_type="image/jpeg")
    assert "Telegram thumbnail" in parts[1]
    assert download.await_args.args == ("thumb",)


@pytest.mark.parametrize("failure", ["corrupt", "oversize", "timeout"])
async def test_still_failure_preserves_text(
    failure: str, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = _media_message("animation")
    download = _download(test_services.bot, b"corrupt", monkeypatch)
    if failure == "oversize":
        message = message.model_copy(
            update={"animation": message.animation.model_copy(update={"file_size": AI_MAX_VIDEO_SIZE_BYTES + 1})}
        )
    if failure == "timeout":
        monkeypatch.setattr(transform_animation, "STILL_TIMEOUT", 0)
    parts = await build_message_parts(
        message, "caption", "", None, True, bot=test_services.bot, redis=test_services.redis
    )
    assert parts == ["caption"]
    if failure == "oversize":
        download.assert_not_awaited()


def test_still_pixel_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "image.gif"
    source.write_bytes(_gif())
    monkeypatch.setattr(animation_still, "MAX_STILL_PIXELS", 1)
    with pytest.raises(ValueError, match="dimensions"):
        animation_still.decode_samples(source, tmp_path, None)
    assert not (tmp_path / "out.jpg").exists()


async def test_download_enforces_actual_bytes_and_deadline(
    test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_download, "AI_MAX_VIDEO_SIZE_BYTES", 3)
    _download(test_services.bot, b"oversized", monkeypatch)
    with pytest.raises(media_download.MediaLimitError):
        await media_download.download_media("id", None, bot=test_services.bot)

    async def stalled(file_id: str, *, destination: BytesIO) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(test_services.bot, "download", stalled)
    monkeypatch.setattr(media_download, "MEDIA_DOWNLOAD_TIMEOUT", 0.001)
    with pytest.raises(TimeoutError):
        await media_download.download_media("id", None, bot=test_services.bot)


async def test_captionless_background_cache_is_metadata_only_and_epoch_fenced(
    test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = _media_message("audio")
    chat = SimpleNamespace(tid=CHAT_TID)
    download = _download(test_services.bot, b"audio", monkeypatch)
    middleware = CacheUserMessagesMiddleware()
    monkeypatch.setattr(
        "sophie_bot.modules.ai.middlewares.cache_user_messages.resolve_chat_mode", AsyncMock(return_value="support")
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.middlewares.cache_user_messages.get_capabilities",
        lambda mode: SimpleNamespace(message_cache=True, memory=False, proactive_replies=False),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.middlewares.cache_user_messages.maybe_run_proactive_reply", AsyncMock())
    await middleware(AsyncMock(), message, {"context": SimpleNamespace(event_chat=chat), "services": test_services})
    rows = await get_cached_messages(CHAT_TID, redis=test_services.redis)
    assert len(rows) == 1 and rows[0].text == ""
    assert rows[0].media.audio.file_id == "attachment"
    assert rows[0].media.audio.file_name == "recording.m4a"
    download.assert_not_awaited()
    epoch = int(await test_services.redis.get(get_context_epoch_key(CHAT_TID)) or 0)
    await reset_modern_context(CHAT_TID, redis=test_services.redis)
    await cache_message(
        None,
        CHAT_TID,
        OTHER_TID,
        2,
        datetime.now(UTC),
        None,
        media=rows[0].media,
        expected_epoch=epoch,
        redis=test_services.redis,
    )
    assert not await get_cached_messages(CHAT_TID, redis=test_services.redis)


@pytest.mark.parametrize("kind", ["audio", "animation", "document"])
@pytest.mark.parametrize("source", ["current", "reply", "background"])
async def test_media_reaches_selected_ai_input(
    kind: str, source: str, context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message(kind)
    _download(context.services.bot, _gif(), monkeypatch)
    monkeypatch.setattr(
        transform_audio,
        "get_mistral_client",
        AsyncMock(
            return_value=SimpleNamespace(
                audio=SimpleNamespace(
                    transcriptions=SimpleNamespace(
                        complete_async=AsyncMock(return_value=SimpleNamespace(text="speech from attachment"))
                    )
                )
            )
        ),
    )
    current = _message(10)
    if source == "current":
        current = attachment.model_copy(update={"message_id": 10, "caption": "current caption"})
    elif source == "reply":
        current = current.model_copy(update={"reply_to_message": attachment})
    else:
        await cache_message(
            "earlier caption",
            CHAT_TID,
            OTHER_TID,
            1,
            attachment.date,
            None,
            media=CachedMedia.from_message(attachment),
            redis=context.services.redis,
        )
    history = await _build(current, context)
    try:
        prompt, messages = history.project_for_candidate(AIModelCandidate(model=TestModel(), model_name="test"))
        parts = [
            *prompt,
            *(
                item
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
                for item in ([part.content] if isinstance(part.content, str) else part.content)
            ),
        ]
        if kind == "audio":
            assert "speech from attachment" in "".join(part for part in parts if isinstance(part, str))
        else:
            assert any(isinstance(part, BinaryContent) and part.media_type == "image/jpeg" for part in parts)
            text_prompt, text_messages = history.project_for_candidate(
                AIModelCandidate(model=TestModel(), model_name="text", supports_images=False)
            )
            assert not any(isinstance(part, BinaryContent) for part in text_prompt)
            assert not any(
                isinstance(item, BinaryContent)
                for message in text_messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
                for item in part.content
            )
        if source == "background":
            assert all("speech from attachment" not in event.messages_json for event in history._state.events)
            assert all("base64" not in event.messages_json for event in history._state.events)
    finally:
        await history.abort()


async def test_old_cache_and_history_media_selection_bounds(
    context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = MessageType.model_validate_json('{"user_id":42,"message_id":1,"text":"old text"}')
    assert old.media is None
    attachment = CachedMedia.from_message(_media_message("audio"))
    for number in range(2, 9):
        await cache_message(
            "caption",
            CHAT_TID,
            OTHER_TID,
            number,
            datetime.now(UTC),
            None,
            media=attachment,
            message_thread_id=99 if number == 8 else None,
            redis=context.services.redis,
        )
    await cache_message(
        "expired",
        CHAT_TID,
        OTHER_TID,
        9,
        datetime.now(UTC) - timedelta(hours=49),
        None,
        media=attachment,
        redis=context.services.redis,
    )
    transcribe = AsyncMock(return_value="historical speech")
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.transform_voice_to_text", transcribe)
    download = _download(context.services.bot, b"", monkeypatch)
    history = await _build(_message(20), context)
    try:
        assert transcribe.await_count == 4
        download.assert_not_awaited()
        assert all(row.message_thread_id != 99 for event in history._state.events for row in event.media_rows)
    finally:
        await history.abort()
    transcribe.reset_mock()
    history = await _build(_message(21), context, include_background=False)
    try:
        # Existing retained background remains available; new cache ingestion is disabled.
        assert transcribe.await_count == 0
    finally:
        await history.abort()


@pytest.mark.parametrize("source", ["reply", "background"])
async def test_legacy_context_resolves_only_selected_media_at_input_build(
    source: str, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message("audio")
    await cache_message(
        "caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=test_services.redis,
    )
    transcription = AsyncMock(return_value="legacy transcript")
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.transform_voice_to_text", transcription)
    history = OldContext(services=test_services)
    await history.add_from_cache(CHAT_TID, fold_background=True)
    transcription.assert_not_awaited()
    current = _message(10)
    if source == "reply":
        current = current.model_copy(update={"reply_to_message": attachment})
    await history.add_from_message(current)
    history.apply_context_block()
    assert "legacy transcript" in str(history.prompt)
    transcription.assert_awaited_once()


async def test_retained_metadata_replays_lazily_and_pruned_media_is_not_extracted(
    context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message("audio")
    await cache_message(
        "earlier caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    transcription = AsyncMock(return_value="cached transcript")
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.transform_voice_to_text", transcription)
    first = await _build(_message(10), context)
    await first.finish_run(_events(first), _message(11))
    transcription.reset_mock()
    second = await _build(_message(12), context)
    try:
        transcription.assert_awaited_once()
        assert "cached transcript" in str(second.message_history)
        stored = second._state.model_dump_json()
        assert "cached transcript" not in stored
        assert "attachment" in stored
    finally:
        await second.abort()
    transcription.reset_mock()
    third = await _build(_message(13), context, token_budget=3000)
    try:
        transcription.assert_not_awaited()
        assert "latest question" in str(third.prompt)
    finally:
        await third.abort()


async def test_failed_cached_extraction_keeps_caption(
    context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message("audio")
    await cache_message(
        "keep this caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.modern_context.transform_voice_to_text",
        AsyncMock(side_effect=transform_audio.EmptyAudioError("empty audio")),
    )
    history = await _build(_message(10), context)
    try:
        assert "keep this caption" in str(history.message_history)
    finally:
        await history.abort()


async def test_cancelled_still_kills_worker_and_cleans_directory(
    test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    _download(test_services.bot, _gif(), monkeypatch)
    entered = asyncio.Event()
    paths: list[Path] = []
    process = MagicMock(returncode=None)

    async def wait() -> int:
        if process.returncode is None:
            entered.set()
            await asyncio.Event().wait()
        return process.returncode

    def kill() -> None:
        process.returncode = -9

    async def spawn(*args: str, **kwargs: object) -> MagicMock:
        paths.append(Path(args[-2]))
        return process

    process.wait = wait
    process.kill = MagicMock(side_effect=kill)
    monkeypatch.setattr(transform_animation.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        transform_animation.extract_animation_samples(_media_message("animation").animation, bot=test_services.bot)
    )
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.kill.assert_called_once()
    assert paths and not paths[0].exists()


@pytest.mark.parametrize("duration", [2, 5, 6, 0])
async def test_mp4_animation_produces_still(
    duration: int, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=1)
        stream.width = 16
        stream.height = 16
        frame = av.VideoFrame(16, 16, format="yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for index in range(6):
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    _download(test_services.bot, buffer.getvalue(), monkeypatch)
    media = _media_message("animation").animation.model_copy(update={"duration": duration})
    image = await transform_animation.extract_animation_samples(media, bot=test_services.bot)
    assert image and image[0].data.startswith(b"\xff\xd8")
    assert [sample.index for sample in image] == ([0] if duration == 2 else [1, 3, 5])


def test_still_packet_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "image.gif"
    source.write_bytes(_gif())
    monkeypatch.setattr(animation_still, "MAX_STILL_PACKETS", 0)
    with pytest.raises(ValueError, match="no usable frame"):
        animation_still.decode_samples(source, tmp_path, None)


async def test_text_model_budget_does_not_reserve_image_tokens(
    context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _download(context.services.bot, b"thumbnail", monkeypatch)
    history = await _build(_media_message("animation", thumbnail=True).model_copy(update={"message_id": 10}), context)
    history._token_budgets += (ModelTokenBudget(_byte_counter("small-text"), 3000),)
    try:
        prompt, _messages = history.project_for_candidate(
            AIModelCandidate(model=TestModel(model_name="small-text"), model_name="small-text", supports_images=False)
        )
        assert not any(isinstance(part, BinaryContent) for part in prompt)
        assert any(isinstance(part, BinaryContent) for part in history.prompt)
    finally:
        await history.abort()


async def test_retained_attachment_metadata_expires_with_cache_ttl(
    context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message("audio")
    await cache_message(
        "caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    transcription = AsyncMock(return_value="speech")
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.transform_voice_to_text", transcription)
    first = await _build(_message(10), context)
    for event in first._state.events:
        for row in event.media_rows:
            row.created_at = datetime.now(UTC) - timedelta(hours=49)
    await first.finish_run(_events(first), _message(11))
    transcription.reset_mock()
    second = await _build(_message(12), context)
    try:
        transcription.assert_not_awaited()
        assert all(not event.media_rows for event in second._state.events)
    finally:
        await second.abort()


def _progression_gif(count: int, duration_ms: int) -> bytes:
    buffer = BytesIO()
    images = [Image.new("RGB", (16, 16), (index * 30, 0, 0)) for index in range(count)]
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=duration_ms, loop=0)
    return buffer.getvalue()


@pytest.mark.parametrize("duration_ms,indices", [(980, [0]), (1000, [1, 2, 4]), (1020, [1, 2, 4])])
@pytest.mark.parametrize("kind", ["document", "animation"])
async def test_duration_threshold_and_exact_frame_order(
    duration_ms: int,
    indices: list[int],
    kind: str,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _media_message(kind, thumbnail=True)
    if message.animation:
        message = message.model_copy(update={"animation": message.animation.model_copy(update={"duration": 0})})
    download = _download(test_services.bot, _progression_gif(5, duration_ms), monkeypatch)
    parts = await build_message_parts(
        message, "untrusted caption", "", None, True, bot=test_services.bot, redis=test_services.redis
    )
    images = [part for part in parts if isinstance(part, BinaryContent)]
    assert len(images) == len(indices)
    colors = []
    for image in images:
        with Image.open(BytesIO(image.data)) as decoded:
            colors.append(decoded.getpixel((0, 0))[0])
    assert all(abs(color - index * 30) < 5 for color, index in zip(colors, indices, strict=True))
    labels = [part for part in parts if isinstance(part, str) and "image follows" in part]
    assert all(f"frame_index={index};" in label for index, label in zip(indices, labels, strict=True))
    assert download.await_args.args == ("attachment",)


@pytest.mark.parametrize("count,indices", [(1, [0]), (2, [1]), (3, [1, 2]), (6, [1, 3, 5])])
async def test_long_metadata_bypasses_thumbnail_and_deduplicates(
    count: int,
    indices: list[int],
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _media_message("animation", thumbnail=True)
    message = message.model_copy(update={"animation": message.animation.model_copy(update={"duration": 5})})
    download = _download(test_services.bot, _progression_gif(count, 1000), monkeypatch)
    samples = await transform_animation.extract_animation_samples(message.animation, bot=test_services.bot)
    assert [sample.index for sample in samples] == indices
    assert all(sample.progression for sample in samples)
    parts = await build_message_parts(
        message, "caption", "", None, True, bot=test_services.bot, redis=test_services.redis
    )
    assert len([part for part in parts if isinstance(part, BinaryContent)]) == len(indices)
    assert all(call.args == ("attachment",) for call in download.await_args_list)


async def test_multiple_animation_groups_and_trusted_system_instruction(
    context: SophieAIToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _download(context.services.bot, _progression_gif(5, 1000), monkeypatch)
    first = _media_message("document").model_copy(update={"message_id": 10})
    second = _media_message("document").model_copy(update={"message_id": 20})
    groups = []
    for message in (first, second):
        parts = await build_message_parts(
            message, "UNTRUSTED_CAPTION", "", None, True, bot=context.services.bot, redis=context.services.redis
        )
        labels = [part for part in parts if isinstance(part, str) and "image follows" in part]
        assert len(labels) == 3
        identifiers = {label.split("group=")[1].split(";")[0] for label in labels}
        assert len(identifiers) == 1
        groups.append(identifiers.pop())
        source = hashlib.sha256(f"{message.chat.id}:{message.message_id}".encode()).hexdigest()
        assert all(f"source_message={source};" in label for label in labels)
        assert f"[End GIF/animation group={groups[-1]}]" == parts[-1]
    assert groups[0] != groups[1]
    history = await _build(first, context)
    try:
        assert transform_animation.ANIMATION_INSTRUCTION in history.instructions
        assert "UNTRUSTED_CAPTION" not in history.instructions
        assert "zero-based frame index 1" in history.instructions
    finally:
        await history.abort()
    legacy = OldContext(services=context.services)
    legacy.add_system("trusted")
    assert transform_animation.ANIMATION_INSTRUCTION in legacy.message_history[0].parts[0].content


@pytest.mark.parametrize("limit", ["MAX_FRAMES", "MAX_DECODED_PIXELS", "MAX_STILL_PACKETS"])
def test_progression_decode_budget_fails_without_manifest(
    limit: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.gif"
    source.write_bytes(_progression_gif(5, 1000))
    monkeypatch.setattr(animation_still, limit, 1)
    with pytest.raises(ValueError):
        animation_still.decode_samples(source, tmp_path, None)
    assert not (tmp_path / "samples.json").exists()


def test_unknown_duration_is_not_treated_as_short(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = av.VideoFrame(16, 16, "rgb24")
    monkeypatch.setattr(animation_still, "frames", lambda source: iter([frame]))
    container = MagicMock()
    container.__enter__.return_value = SimpleNamespace(
        streams=SimpleNamespace(video=[SimpleNamespace(duration=None)]), duration=None
    )
    monkeypatch.setattr(animation_still.av, "open", lambda source: container)
    with pytest.raises(ValueError, match="duration is unknown"):
        animation_still.decode_samples(tmp_path / "input", tmp_path, None)
    assert not (tmp_path / "samples.json").exists()


def test_worker_installs_memory_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "input.gif"
    source.write_bytes(_progression_gif(5, 1000))
    limits = MagicMock()
    monkeypatch.setattr(resource, "setrlimit", limits)
    monkeypatch.setattr(sys, "argv", ["animation_still.py", str(source), str(tmp_path), "unknown"])
    runpy.run_path(animation_still.__file__, run_name="__main__")
    limits.assert_called_once_with(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    assert (tmp_path / "samples.json").exists()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("kind", ["video", "video_note"])
async def test_background_video_never_enters_decoder(
    legacy: bool, kind: str, context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message(kind, thumbnail=True)
    await cache_message(
        "video caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    download = _download(context.services.bot, b"thumbnail", monkeypatch)
    decoder = MagicMock(side_effect=AssertionError("background video must not decode"))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.transform_video.av.open", decoder)
    if legacy:
        history = OldContext(services=context.services)
        await history.add_from_cache(CHAT_TID, fold_background=True)
        download.assert_not_awaited()
        await history.add_from_message(_message(10))
        assert "video caption" in str(history.prompt)
    else:
        history = await _build(_message(10), context)
        try:
            assert "video caption" in str(history.message_history)
        finally:
            await history.abort()
    decoder.assert_not_called()
    download.assert_awaited_once()
    assert download.await_args.args == ("thumb",)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("source", ["current", "reply"])
@pytest.mark.parametrize("kind", ["video", "video_note"])
async def test_explicit_video_still_transcribes(
    legacy: bool, source: str, kind: str, context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message(kind)
    # A cached explicit reply takes a different path in OldContext.
    await cache_message(
        "video caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    extract = AsyncMock(return_value=b"encoded audio")
    monkeypatch.setattr("sophie_bot.modules.ai.utils.transform_video.extract_audio_from_video", extract)
    complete = AsyncMock(return_value=SimpleNamespace(text="explicit video speech"))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.transform_video.get_mistral_client",
        AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=complete)))
        ),
    )
    current = (
        attachment.model_copy(update={"message_id": 10})
        if source == "current"
        else _message(10).model_copy(update={"reply_to_message": attachment})
    )
    if legacy:
        history = OldContext(services=context.services)
        await history.add_from_cache(CHAT_TID, fold_background=True)
        await history.add_from_message(current)
        assert "explicit video speech" in str(history.prompt)
    else:
        history = await _build(current, context)
        try:
            assert "explicit video speech" in str(history.prompt)
        finally:
            await history.abort()
    extract.assert_awaited_once()
    complete.assert_awaited_once()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("kind", ["voice", "audio", "video", "video_note"])
@pytest.mark.parametrize(
    "failure", ["telegram", "connection", "os", "timeout", "empty", "provider", "no_response", "httpx", "httpx2"]
)
async def test_cached_media_failure_does_not_poison_later_text_requests(
    legacy: bool, kind: str, failure: str, context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    if kind in {"video", "video_note"} and failure in {"empty", "provider", "no_response", "httpx", "httpx2"}:
        pytest.skip("background videos only download thumbnails")
    attachment = _media_message(kind, thumbnail=kind in {"video", "video_note"})
    await cache_message(
        "keep media caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    errors = {
        "telegram": TelegramBadRequest(method=GetFile(file_id="attachment"), message="unavailable"),
        "connection": ClientConnectionError("download disconnected"),
        "os": OSError("download failed"),
        "timeout": TimeoutError("download timed out"),
        "provider": SDKError("provider unavailable", httpx2.Response(503, text="unavailable")),
        "no_response": NoResponseError(),
        "httpx": httpx.ConnectError("provider disconnected"),
        "httpx2": httpx2.ReadTimeout("provider timed out"),
    }
    download = _download(context.services.bot, b"" if failure == "empty" else b"audio", monkeypatch)
    if failure in {"telegram", "connection", "os", "timeout"}:
        download.side_effect = errors[failure]
    complete = AsyncMock(return_value=SimpleNamespace(text="recovered speech"))
    if failure in {"provider", "no_response", "httpx", "httpx2"}:
        complete.side_effect = errors[failure]
    monkeypatch.setattr(
        transform_audio,
        "get_mistral_client",
        AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=complete)))
        ),
    )
    for message_id in (10, 12):
        if legacy:
            history = OldContext(services=context.services)
            await history.add_from_cache(CHAT_TID, fold_background=True)
            await history.add_from_message(_message(message_id))
            history.apply_context_block()
            assert "keep media caption" in str(history.prompt)
            assert "latest question" in str(history.prompt)
        else:
            history = await _build(_message(message_id), context)
            assert "keep media caption" in str(history.message_history)
            assert "latest question" in str(history.prompt)
            assert history._state.watermark == message_id
            await history.finish_run(_events(history), _message(message_id + 1))
    # Failed media stays optional, and can be retried successfully in a later request.
    _download(context.services.bot, b"recovered media", monkeypatch)
    complete.side_effect = None
    if legacy:
        history = OldContext(services=context.services)
        await history.add_from_cache(CHAT_TID, fold_background=True)
        await history.add_from_message(_message(14))
        result = str(history.prompt)
    else:
        history = await _build(_message(14), context)
        result = str(history.message_history)
        await history.abort()
    if kind in {"audio", "voice"}:
        assert "recovered speech" in result


@pytest.mark.parametrize("legacy", [False, True])
async def test_cached_media_unexpected_failure_propagates(
    legacy: bool, context: SophieAIToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = _media_message("audio")
    await cache_message(
        "caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    download = _download(context.services.bot, b"audio", monkeypatch)
    download.side_effect = ValueError("programming error")
    with pytest.raises(ValueError, match="programming error"):
        if legacy:
            history = OldContext(services=context.services)
            await history.add_from_cache(CHAT_TID, fold_background=True)
            await history.add_from_message(_message(10))
        else:
            await _build(_message(10), context)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("media_count", [1, 4])
async def test_pending_background_transcriptions_share_budget_and_can_retry(
    legacy: bool,
    media_count: int,
    context: SophieAIToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.ChatModel.get_by_tid", AsyncMock(return_value=None))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.BACKGROUND_MEDIA_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.BACKGROUND_MEDIA_BUDGET_SECONDS", 0.15)
    attachment = _media_message("audio")
    for message_id in range(1, media_count + 1):
        await cache_message(
            f"caption {message_id}",
            CHAT_TID,
            OTHER_TID,
            message_id,
            attachment.date,
            None,
            media=CachedMedia.from_message(attachment),
            redis=context.services.redis,
        )
    await cache_message(
        "unrelated background text", CHAT_TID, OTHER_TID, 5, attachment.date, None, redis=context.services.redis
    )
    _download(context.services.bot, b"audio", monkeypatch)
    cancellations: list[bool] = []

    async def pending_transcription(**kwargs: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancellations.append(True)

    complete = AsyncMock(side_effect=pending_transcription)
    monkeypatch.setattr(
        transform_audio,
        "get_mistral_client",
        AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=complete)))
        ),
    )
    async with asyncio.timeout(2):
        if legacy:
            history = OldContext(services=context.services)
            await history.add_from_cache(CHAT_TID, fold_background=True)
            await history.add_from_message(_message(10))
            history.apply_context_block()
            result = str(history.prompt)
        else:
            history = await _build(_message(10), context)
            result = str(history.message_history) + str(history.prompt)
            await history.finish_run(_events(history), _message(11))
    assert complete.await_count == min(media_count, 2)
    assert len(cancellations) == complete.await_count
    for message_id in range(1, media_count + 1):
        assert f"caption {message_id}" in result
    assert "unrelated background text" in result
    assert "latest question" in result

    # Neither timeout nor aggregate exhaustion persists as a failed transcript.
    complete.side_effect = None
    complete.return_value = SimpleNamespace(text="recovered speech")
    if legacy:
        history = OldContext(services=context.services)
        await history.add_from_cache(CHAT_TID, fold_background=True)
        await history.add_from_message(_message(12))
        result = str(history.prompt)
    else:
        history = await _build(_message(12), context)
        result = str(history.message_history)
        await history.abort()
    assert "recovered speech" in result
    assert complete.await_count == min(media_count, 2) + media_count


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("source", ["current", "reply"])
async def test_explicit_audio_does_not_use_background_deadlines(
    legacy: bool,
    source: str,
    context: SophieAIToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.ChatModel.get_by_tid", AsyncMock(return_value=None))
    # An exhausted background budget must still allow explicit audio transcription.
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.BACKGROUND_MEDIA_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.modern_context.BACKGROUND_MEDIA_BUDGET_SECONDS", 0)
    attachment = _media_message("audio")
    await cache_message(
        "audio caption",
        CHAT_TID,
        OTHER_TID,
        1,
        attachment.date,
        None,
        media=CachedMedia.from_message(attachment),
        redis=context.services.redis,
    )
    _download(context.services.bot, b"audio", monkeypatch)

    async def slow_transcription(**kwargs: object) -> SimpleNamespace:
        await asyncio.sleep(0.02)
        return SimpleNamespace(text="explicit audio speech")

    complete = AsyncMock(side_effect=slow_transcription)
    monkeypatch.setattr(
        transform_audio,
        "get_mistral_client",
        AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=complete)))
        ),
    )
    current = (
        attachment.model_copy(update={"message_id": 10})
        if source == "current"
        else _message(10).model_copy(update={"reply_to_message": attachment})
    )
    if legacy:
        history = OldContext(services=context.services)
        await history.add_from_cache(CHAT_TID, fold_background=True)
        await history.add_from_message(current)
        assert "explicit audio speech" in str(history.prompt)
    else:
        history = await _build(current, context)
        try:
            assert "explicit audio speech" in str(history.prompt)
        finally:
            await history.abort()
    complete.assert_awaited_once()
