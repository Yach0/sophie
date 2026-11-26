from __future__ import annotations

import asyncio
import json
from io import BufferedReader
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from mistralai.client.errors import NoResponseError, SDKError

from sophie_bot.modules.ai.utils import transcription, transform_video
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _send_chatbot_ai_failure_reply
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.modern_context import build_cached_message_parts, build_message_parts
from sophie_bot.modules.error.handlers.error import SophieErrorHandler
from sophie_bot.services.application import ApplicationServices
from tests.test_ai_failure_reply_markup import _bot
from tests.test_ai_media_pathways import _download, _media_message

pytestmark = pytest.mark.usefixtures("native_catalog")

BILLING_BODY = '{"message":"No API budget configured","type":"billing_no_api_budget_configured","code":"2314"}'


def billing_failure() -> SDKError:
    return SDKError("API error occurred: Status 402\n" + BILLING_BODY, httpx.Response(402, text=BILLING_BODY))


@pytest.mark.parametrize("kind", ["voice", "audio", "video", "video_note"])
@pytest.mark.parametrize("cached_reply", [False, True])
async def test_explicit_transcription_billing_failure_is_concise(
    kind: str,
    cached_reply: bool,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    runtime_sentry_events: list[dict[str, Any]],
) -> None:
    failure = billing_failure()
    complete = AsyncMock(side_effect=failure)
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=complete)))
    monkeypatch.setattr(transcription, "get_mistral_client", AsyncMock(return_value=client))
    monkeypatch.setattr(transform_video, "extract_audio_from_video", AsyncMock(return_value=b"audio"))
    _download(test_services.bot, b"audio", monkeypatch)
    warning = Mock()
    monkeypatch.setattr(transcription.log, "warning", warning)
    with pytest.raises(AIRequestFailed) as raised:
        if cached_reply:
            await build_cached_message_parts(
                _media_message(kind),
                "caption",
                bot=test_services.bot,
                redis=test_services.redis,
                transcribe_video=True,
                budget=None,
            )
        else:
            await build_message_parts(
                _media_message(kind), "caption", "", None, True, bot=test_services.bot, redis=test_services.redis
            )
    assert raised.value.__cause__ is failure
    bot = _bot()
    await _send_chatbot_ai_failure_reply(_media_message(kind).as_(bot), None, raised.value)
    text = bot.capture.all_requests[-1].text
    (event,) = runtime_sentry_events
    assert raised.value.sentry_event_id == event["event_id"]
    assert SophieErrorHandler.capture_sentry(raised.value) == event["event_id"]
    assert len(runtime_sentry_events) == 1
    assert event["event_id"] in text
    assert event["tags"]["ai.origin"] == "explicit"
    assert event["tags"]["ai.operation"] == "transcription"
    assert event["tags"]["ai.status_code"] == "402"
    assert "exception" not in event
    assert "billing_no_api_budget_configured" not in json.dumps(event)
    assert "AI request failed" in text
    assert "Audio transcription could not be completed. Please try again later." in text
    assert len(text) < 250
    assert "billing_no_api_budget_configured" not in text
    assert "2314" not in text
    warning.assert_called_once_with(
        "Audio transcription failed",
        model="voxtral-mini-latest",
        error_type="SDKError",
        status_code=402,
    )
    complete.assert_awaited_once()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 408, 422, 429, 500, 502, 503, 504])
def test_known_transcription_provider_failures_translate(status: int) -> None:
    with pytest.raises(AIRequestFailed), transcription.transcription_errors():
        raise SDKError("private provider body", httpx.Response(status))


@pytest.mark.parametrize(
    "failure",
    [NoResponseError(), httpx.ConnectError("private endpoint"), TimeoutError("private endpoint")],
)
def test_transcription_transport_failures_translate(failure: Exception) -> None:
    with pytest.raises(AIRequestFailed), transcription.transcription_errors():
        raise failure


@pytest.mark.parametrize("failure", [ValueError("bug"), RuntimeError("unexpected bug")])
def test_unrelated_transcription_errors_propagate(failure: Exception) -> None:
    with pytest.raises(type(failure)) as raised, transcription.transcription_errors():
        raise failure
    assert raised.value is failure


async def test_video_transcription_closes_audio_on_cancellation(
    test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded_audio: list[BufferedReader] = []

    async def transcribe(**kwargs: Any) -> None:
        uploaded_audio.append(kwargs["file"]["content"])
        raise asyncio.CancelledError

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=transcribe)))
    monkeypatch.setattr(transcription, "get_mistral_client", AsyncMock(return_value=client))
    monkeypatch.setattr(transform_video, "extract_audio_from_video", AsyncMock(return_value=b"audio"))
    with pytest.raises(asyncio.CancelledError):
        await transform_video.transform_video_to_text(
            _media_message("video").video, bot=test_services.bot, redis=test_services.redis
        )
    assert uploaded_audio[0].closed
