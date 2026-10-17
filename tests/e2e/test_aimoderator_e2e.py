"""End-to-end tests for /aimoderator: show the picker and cycle a category's detection level."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from aiogram import F, Router
from aiogram.types import Chat, InlineKeyboardMarkup, Message, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import RequestType
from mistralai.client.errors import SDKError
from openai import InternalServerError, RateLimitError
from tenacity import wait_none

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.db.models.ai.ai_moderator import DetectionLevel
from sophie_bot.modules.ai.callbacks import AIModeratorCategoryCallback
from sophie_bot.modules.ai.utils import ai_errors
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory
from sophie_bot.modules.ai.utils.moderation.settings import get_levels, get_moderator_settings
from sophie_bot.modules.error.handlers.error import SophieErrorHandler
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id


def _callbacks(requests: list) -> list[str]:
    markup_data = next(request.params.get("reply_markup") for request in requests if request.params.get("reply_markup"))
    markup = InlineKeyboardMarkup.model_validate(markup_data)
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_aimoderator_shows_a_button_per_category(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="aimoderator", from_user=admin, chat=group)

    callbacks = _callbacks(requests)
    assert len(callbacks) == len(ModerationCategory)
    assert {AIModeratorCategoryCallback.unpack(data).category for data in callbacks} == {
        category.value for category in ModerationCategory
    }


@pytest.mark.asyncio
async def test_pressing_a_category_cycles_and_persists_its_level(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Cycle Group")
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="AI Moderator", from_user=bot_user, chat=group)
    callback_data = AIModeratorCategoryCallback(category=ModerationCategory.PII.value).pack()

    # An unconfigured chat starts at NORMAL, so one press moves it to HIGH and the next wraps to OFF.
    await test_client.send_callback(callback_data, from_user=admin, message=picker)
    levels = get_levels(await get_moderator_settings(chat.iid))
    assert levels[ModerationCategory.PII] == DetectionLevel.HIGH
    assert levels[ModerationCategory.SEXUAL] == DetectionLevel.NORMAL

    await test_client.send_callback(callback_data, from_user=admin, message=picker)
    levels = get_levels(await get_moderator_settings(chat.iid))
    assert levels[ModerationCategory.PII] == DetectionLevel.OFF


@pytest.mark.asyncio
async def test_aimoderator_requires_admin(test_client: TestClient) -> None:
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Auth Group")
    stranger = test_client.create_user(user_id=next_user_id(), first_name="Stranger", username="aimod_stranger")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)

    requests = await test_client.send_command(command="aimoderator", from_user=stranger.user, chat=group)

    assert not any(request.params.get("reply_markup") for request in requests), (
        "A non-admin should not get the category picker"
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_change_a_category(test_client: TestClient) -> None:
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="AIModerator Callback Auth Group")
    stranger = test_client.create_user(user_id=next_user_id(), first_name="Outsider", username="aimod_outsider")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    picker = MessageFactory.create(text="AI Moderator", from_user=bot_user, chat=group)

    await test_client.send_callback(
        AIModeratorCategoryCallback(category=ModerationCategory.PII.value).pack(),
        from_user=stranger.user,
        message=picker,
    )

    assert await get_moderator_settings(chat.iid) is None, "A non-admin press must not persist anything"


_CLASSIFIER_INPUT = "Private moderation regression message"
_LATER_HANDLER_REPLY = "LATER_HANDLER_REACHED"


def _attach_later_handler(extra_router: Callable[[Router], Router]) -> None:
    router = Router(name="moderation_unavailable_later_handler")

    @router.message(F.text == _CLASSIFIER_INPUT)
    async def later_handler(message: Message) -> None:
        await message.reply(_LATER_HANDLER_REPLY)

    extra_router(router)


async def _enable_moderation(
    test_client: TestClient,
    services: ApplicationServices,
    mode: AIMode,
    provider: str = "mistral",
) -> tuple[User, Chat]:
    member, group, _model = await create_test_user_and_group(test_client, group_title="Moderation availability")
    await grant_bot_admin(group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, mode)
    await set_chat_override("ai_moderation", group.id, True, redis=services.redis)
    await set_chat_override("ai_moderation_provider", group.id, provider, redis=services.redis)
    await set_chat_override("ai_moderation_notice_delete_after_seconds", group.id, 0, redis=services.redis)
    return member, group


def _provider_failure(provider: str, status_code: int) -> Exception:
    if provider == "mistral":
        response = httpx.Response(
            status_code,
            request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/moderations"),
            text='{"error":{"message":"backend_out_of_capacity"}}',
        )
        return SDKError("API error occurred", response)
    response = httpx2.Response(
        status_code,
        request=httpx2.Request("POST", "https://api.openai.com/v1/moderations"),
    )
    if status_code == 429:
        return RateLimitError("Overloaded", response=response, body=None)
    return InternalServerError("Service unavailable", response=response, body=None)


def _patch_classifier_client(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    classify: AsyncMock,
) -> None:
    if provider == "mistral":
        client = SimpleNamespace(classifiers=SimpleNamespace(moderate_chat_async=classify))
    else:
        client = SimpleNamespace(moderations=SimpleNamespace(create=classify))
    monkeypatch.setattr(
        f"sophie_bot.modules.ai.utils.moderation.providers.{provider}.get_{provider}_client",
        AsyncMock(return_value=client),
    )


@pytest.mark.parametrize("mode", [AIMode.moderation, AIMode.support])
@pytest.mark.parametrize("provider", ["mistral", "openai"])
@pytest.mark.parametrize("status_code", [429, 503])
async def test_moderation_exhausted_provider_failure_is_silent_and_stops_all_handlers(
    test_client: TestClient,
    test_services: ApplicationServices,
    extra_router: Callable[[Router], Router],
    monkeypatch: pytest.MonkeyPatch,
    mode: AIMode,
    provider: str,
    status_code: int,
) -> None:
    member, group = await _enable_moderation(test_client, test_services, mode, provider)
    _attach_later_handler(extra_router)
    classify = AsyncMock(side_effect=_provider_failure(provider, status_code))
    _patch_classifier_client(monkeypatch, provider, classify)
    monkeypatch.setattr(ai_errors, "AI_REQUEST_RETRY_WAIT", wait_none())
    framework_errors: list[Exception] = []

    def capture_framework_error(error: Exception) -> None:
        framework_errors.append(error)

    monkeypatch.setattr(SophieErrorHandler, "capture_sentry", staticmethod(capture_framework_error))

    requests = await test_client.send_message(text=_CLASSIFIER_INPUT, from_user=member, chat=group)

    assert not any(request.request_type == RequestType.SEND_MESSAGE for request in requests)
    assert not framework_errors
    assert classify.await_count == ai_errors.AI_REQUEST_RETRY_ATTEMPTS
    assert not any(
        request.request_type
        in {
            RequestType.DELETE_MESSAGE,
            RequestType.BAN_CHAT_MEMBER,
            RequestType.RESTRICT_CHAT_MEMBER,
        }
        for request in requests
    )
    assert not await get_cached_messages(group.id, redis=test_services.redis)


@pytest.mark.parametrize("configuration_error", [False, True], ids=["unknown-error", "configuration-error"])
async def test_moderation_unknown_failures_remain_framework_errors(
    test_client: TestClient,
    test_services: ApplicationServices,
    extra_router: Callable[[Router], Router],
    monkeypatch: pytest.MonkeyPatch,
    configuration_error: bool,
) -> None:
    member, group = await _enable_moderation(test_client, test_services, AIMode.moderation)
    _attach_later_handler(extra_router)
    failure = _provider_failure("mistral", 401) if configuration_error else RuntimeError("Classifier programming bug")
    classify = AsyncMock(side_effect=failure)
    _patch_classifier_client(monkeypatch, "mistral", classify)

    requests = await test_client.send_message(text=_CLASSIFIER_INPUT, from_user=member, chat=group)

    texts = [request.params["text"] for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert texts
    assert not any(_LATER_HANDLER_REPLY in text for text in texts)
    assert classify.await_count == 1
    assert not any(
        request.request_type
        in {
            RequestType.DELETE_MESSAGE,
            RequestType.BAN_CHAT_MEMBER,
            RequestType.RESTRICT_CHAT_MEMBER,
        }
        for request in requests
    )
