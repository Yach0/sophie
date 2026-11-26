from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from aiogram.types import Chat
from mistralai.client.sdk import Mistral
from openai import AsyncOpenAI

from sophie_bot.db.models.ai.ai_catalog import AICatalogModelModel, AIModelRole
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.modules.ai.api import catalog as catalog_api
from sophie_bot.modules.ai.api.catalog_schemas import ModelCreate
from sophie_bot.modules.ai.utils import transcription, transform_audio, transform_video
from sophie_bot.modules.ai.utils.ai_catalog import bump_version, load_catalog
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.moderation import ModerationCategory, check_moderator
from sophie_bot.modules.ai.utils.moderation.settings import get_enabled
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.migrations import MigrationResources, _bind_migration_resources
from sophie_bot.utils.feature_flags import delete_chat_override, get_chat_override, set_chat_override, set_rollout
from tests.test_ai_media_pathways import _download, _media_message
from tests.utils.native_catalog import select_native_models

pytestmark = pytest.mark.usefixtures("native_catalog")


@pytest.fixture
async def native_clients(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[tuple[str, bytes]]]:
    requests: list[tuple[str, bytes]] = []

    def mistral_response(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.content))
        if "transcriptions" in request.url.path:
            return httpx.Response(
                200, json={"model": "voxtral-mini-latest", "text": "spoken words\n", "usage": {}, "language": "en"}
            )
        return httpx.Response(
            200,
            json={
                "id": "mod",
                "model": "mistral-moderation-latest",
                "results": [
                    {
                        "flagged": False,
                        "category_scores": {"sexual": 0.3, "pii": 0.9, "health": 0.9},
                        "categories": {},
                    }
                ],
            },
        )

    def openai_response(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.url.path, request.content))
        if "transcriptions" in request.url.path:
            return httpx2.Response(200, json={"text": "spoken words\n"})
        return httpx2.Response(
            200,
            json={
                "id": "mod",
                "model": "omni-moderation-latest",
                "results": [
                    {
                        "flagged": False,
                        "category_scores": {"sexual": 0.3, "sexual/minors": 0.2, "self-harm": None},
                        "categories": {},
                        "category_applied_input_types": {},
                    }
                ],
            },
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(mistral_response)) as mistral_http,
        AsyncOpenAI(
            api_key="offline",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(openai_response)),
        ) as openai_client,
    ):
        mistral_client = Mistral(api_key="offline", async_client=mistral_http)
        monkeypatch.setattr(transcription, "get_mistral_client", AsyncMock(return_value=mistral_client))
        monkeypatch.setattr(transcription, "get_openai_client", AsyncMock(return_value=openai_client))
        monkeypatch.setattr(
            "sophie_bot.modules.ai.utils.moderation.providers.mistral.get_mistral_client",
            AsyncMock(return_value=mistral_client),
        )
        monkeypatch.setattr(
            "sophie_bot.modules.ai.utils.moderation.providers.openai.get_openai_client",
            AsyncMock(return_value=openai_client),
        )
        yield requests


@pytest.mark.parametrize("provider", ["mistral", "openai"])
@pytest.mark.parametrize("kind", ["voice", "audio", "video", "video_note"])
async def test_native_transcription_matrix_selection_at_sdk_boundary(
    provider: str,
    kind: str,
    native_clients: list[tuple[str, bytes]],
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await select_native_models(provider, redis=test_services.redis)
    _download(test_services.bot, b"audio payload", monkeypatch)
    monkeypatch.setattr(transform_video, "extract_audio_from_video", AsyncMock(return_value=b"audio payload"))
    message = _media_message(kind)
    if kind in {"voice", "audio"}:
        text = await transform_audio.transform_voice_to_text(
            message.voice or message.audio, bot=test_services.bot, redis=test_services.redis
        )
    else:
        text = await transform_video.transform_video_to_text(
            message.video or message.video_note, bot=test_services.bot, redis=test_services.redis
        )
    assert text == "spoken words"
    ((path, body),) = native_clients
    assert path == "/v1/audio/transcriptions"
    assert b"audio payload" in body
    assert (b"voxtral-mini-latest" if provider == "mistral" else b"gpt-transcribe") in body
    assert (b"recording.m4a" if kind == "audio" else b"audio.ogg") in body
    assert (b"audio/mp4" if kind == "audio" else b"audio/ogg") in body


@pytest.mark.parametrize("provider", ["mistral", "openai"])
@pytest.mark.parametrize("mode", [AIMode.entertainment, AIMode.support, AIMode.moderation])
async def test_moderation_matrix_selection_keeps_native_categories_and_thresholds(
    provider: str,
    mode: AIMode,
    native_clients: list[tuple[str, bytes]],
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await AICatalogModelModel.delete_all()
    await test_services.db.database["ai_native_catalog_seed_backup"].delete_many({})
    await FeatureFlagOverride.set_override("ai_moderation_provider", provider)
    migration = importlib.import_module("sophie_bot.db.migrations.20261126_142335_seed_native_ai_services")
    resources = MigrationResources(database=test_services.db, redis=test_services.redis)
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    await load_catalog(redis=test_services.redis)
    chat = await ChatModel.upsert_group(Chat(id=-100987654321, type="supergroup", title="Native moderation"))
    await AIModeModel.set_mode(chat, mode)
    settings = await AIModeratorModel.set_state(chat, True)
    assert get_enabled(settings, mode)
    history = SimpleNamespace(to_moderation=[{"role": "user", "content": "classify me"}], add_from_message=AsyncMock())
    monkeypatch.setattr("sophie_bot.modules.ai.utils.moderation.OldContext", lambda **kwargs: history)
    result = await check_moderator(
        _media_message("voice"), settings=settings, chat_tid=chat.tid, services=test_services
    )
    if provider == "mistral":
        assert result.triggered == frozenset({ModerationCategory.PII, ModerationCategory.HEALTH})
        assert result.triggered_native == frozenset({"pii", "health"})
        assert result.scores == {"sexual": 0.3, "pii": 0.9, "health": 0.9}
    else:
        assert result.triggered == frozenset({ModerationCategory.SEXUAL})
        assert result.triggered_native == frozenset({"sexual/minors"})
        assert result.scores == {"sexual": 0.3, "sexual/minors": 0.2}
    ((path, body),) = native_clients
    assert path == ("/v1/chat/moderations" if provider == "mistral" else "/v1/moderations")
    payload = json.loads(body)
    assert payload["model"] == ("mistral-moderation-latest" if provider == "mistral" else "omni-moderation-latest")
    assert "classify me" in body.decode()


async def test_native_purposes_appear_in_operator_matrix(test_services: ApplicationServices) -> None:
    meta = await catalog_api.get_meta()
    assert "transcription" in meta.mode_purposes["support"]
    assert "moderation" in meta.mode_purposes["support"]
    assert "moderation" in meta.mode_purposes["moderation"]
    assert "moderation" in meta.mode_purposes["entertainment"]
    resolution = await catalog_api.get_resolution(services=test_services)
    assert resolution.per_mode["support"]["transcription"].model == "mistral/voxtral-mini-latest"


@pytest.mark.parametrize("provider", ["mistral", "openai"])
async def test_transcription_selected_upstream_name_and_error_provenance(
    provider: str,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await select_native_models(provider, redis=test_services.redis)
    async for model in AICatalogModelModel.find(AICatalogModelModel.provider == provider):
        if any(role.purpose.value == "transcription" for role in model.roles):
            model.api_name = "operator-transcription-model"
            await model.save()
    await bump_version(redis=test_services.redis)
    request = httpx2.Request("POST", "https://offline.invalid/v1/audio/transcriptions")
    async with AsyncOpenAI(
        api_key="offline",
        max_retries=0,
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(402, json={"error": {"message": "private"}}))
        ),
    ) as client:
        if provider == "openai":
            monkeypatch.setattr(transcription, "get_openai_client", AsyncMock(return_value=client))
        else:
            failure = httpx.HTTPStatusError(
                "private", request=httpx.Request("POST", str(request.url)), response=httpx.Response(402)
            )
            monkeypatch.setattr(
                transcription,
                "get_mistral_client",
                AsyncMock(
                    return_value=SimpleNamespace(
                        audio=SimpleNamespace(
                            transcriptions=SimpleNamespace(complete_async=AsyncMock(side_effect=failure))
                        )
                    )
                ),
            )
        with pytest.raises(AIRequestFailed) as raised:
            await transcription.transcribe_audio(b"audio", "audio.ogg", "audio/ogg", redis=test_services.redis)
    assert raised.value.ai_error_context.model_name == "operator-transcription-model"


async def test_disabled_native_model_does_not_fall_back_to_chat(test_services: ApplicationServices) -> None:
    await AICatalogModelModel.find(AICatalogModelModel.name == "mistral/voxtral-mini-latest").update(
        {"$set": {"enabled": False}}
    )
    await bump_version(redis=test_services.redis)
    with pytest.raises(ValueError, match="support:transcription"):
        await transcription.transcribe_audio(b"audio", "audio.ogg", "audio/ogg", redis=test_services.redis)


@pytest.mark.parametrize("purpose", ["transcription", "moderation"])
async def test_operator_priority_selects_native_endpoint(
    purpose: str,
    native_clients: list[tuple[str, bytes]],
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = "gpt-transcribe" if purpose == "transcription" else "omni-moderation-latest"
    await catalog_api.create_model(
        ModelCreate(
            name="operator/native-model",
            provider="openai",
            api_name=upstream,
            roles=[AIModelRole(mode=AIMode.support, purpose=purpose, priority=-1)],
        ),
        services=test_services,
    )
    matrix = await catalog_api.get_resolution(services=test_services)
    assert matrix.per_mode["support"][purpose].model == "operator/native-model"
    if purpose == "transcription":
        await transcription.transcribe_audio(b"audio", "audio.ogg", "audio/ogg", redis=test_services.redis)
    else:
        history = SimpleNamespace(to_moderation=[{"role": "user", "content": "test"}], add_from_message=AsyncMock())
        monkeypatch.setattr("sophie_bot.modules.ai.utils.moderation.OldContext", lambda **kwargs: history)
        await check_moderator(_media_message("voice"), services=test_services)
    ((path, body),) = native_clients
    assert path == ("/v1/audio/transcriptions" if purpose == "transcription" else "/v1/moderations")
    assert upstream.encode() in body


@pytest.mark.parametrize("pin_source", ["manual", "rollout"])
async def test_legacy_chat_provider_pin_keeps_native_policy_until_cleared(
    pin_source: str,
    native_clients: list[tuple[str, bytes]],
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await catalog_api.create_model(
        ModelCreate(
            name="openai/omni-moderation-latest",
            provider="openai",
            api_name="omni-moderation-latest",
            roles=[AIModelRole(mode=AIMode.support, purpose="moderation", priority=10)],
        ),
        services=test_services,
    )
    chat = await ChatModel.upsert_group(Chat(id=-100987654321, type="supergroup", title="Pinned moderation"))
    await AIModeModel.set_mode(chat, AIMode.support)
    if pin_source == "manual":
        await set_chat_override("ai_moderation_provider", chat.tid, "openai", redis=test_services.redis)
    else:
        await set_rollout("ai_moderation_provider", 100, "openai", redis=test_services.redis)
    history = SimpleNamespace(to_moderation=[{"role": "user", "content": "test"}], add_from_message=AsyncMock())
    monkeypatch.setattr("sophie_bot.modules.ai.utils.moderation.OldContext", lambda **kwargs: history)
    pinned = await check_moderator(_media_message("voice"), chat_tid=chat.tid, services=test_services)
    assert pinned.triggered_native == frozenset({"sexual/minors"})
    assert await get_chat_override("ai_moderation_provider", chat.tid, redis=test_services.redis) == "openai"
    if pin_source == "rollout":
        await set_rollout("ai_moderation_provider", 0, "openai", redis=test_services.redis)
    await delete_chat_override("ai_moderation_provider", chat.tid, redis=test_services.redis)
    unpinned = await check_moderator(_media_message("voice"), chat_tid=chat.tid, services=test_services)
    assert unpinned.triggered_native == frozenset({"pii", "health"})
    assert [path for path, _body in native_clients] == ["/v1/moderations", "/v1/chat/moderations"]


async def test_native_selection_rejects_chat_providers(test_services: ApplicationServices) -> None:
    await catalog_api.create_model(
        ModelCreate(
            name="chat-model",
            provider="openrouter",
            roles=[AIModelRole(mode="support", purpose="transcription", priority=-1)],
        ),
        services=test_services,
    )
    # A configured chat provider cannot turn a native purpose into a chat prompt.
    await catalog_api.create_provider(
        catalog_api.ProviderCreate(name="openrouter", kind="openrouter"), services=test_services
    )
    with pytest.raises(ValueError, match="requires a native"):
        await transcription.transcribe_audio(b"audio", "audio.ogg", "audio/ogg", redis=test_services.redis)
