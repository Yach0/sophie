"""End-to-end tests for AI module commands.

Covers: /enableai, /aimoderator, /ai_summaries, /ai_note_titles, /aiusage, /aireset, /aiprovider, /aitranslate
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_usage_service import (
    ChatUsageBreakdownItem,
    ChatUsageView,
)
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT, AI_FEATURE_TRANSLATE
from tests.e2e.helpers import grant_admin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_ai_moderation_off(stack: ExitStack) -> None:
    """Keep the AI moderator out of the way: it now runs whenever the chat's mode enables it."""
    stack.enter_context(
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_enabled", AsyncMock(return_value=False))
    )


def _apply_ai_admin_patches(stack: ExitStack) -> None:
    """Enter patches that bypass the AI-enabled and quota filters.

    Admin rights are real ChatAdminModel state -- see `grant_admin`.
    """
    _apply_ai_moderation_off(stack)
    stack.enter_context(
        patch(
            "sophie_bot.modules.ai.middlewares.cache_user_messages.resolve_chat_mode",
            AsyncMock(return_value=AIMode.support),
        )
    )
    stack.enter_context(
        patch("sophie_bot.modules.ai.filters.quota.check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    )
    stack.enter_context(patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)))


def _apply_ai_non_admin_patches(stack: ExitStack) -> None:
    """Enter patches that deny admin but pass AI-enabled and quota filters."""
    _apply_ai_moderation_off(stack)
    stack.enter_context(
        patch(
            "sophie_bot.modules.ai.middlewares.cache_user_messages.resolve_chat_mode",
            AsyncMock(return_value=AIMode.support),
        )
    )
    stack.enter_context(
        patch("sophie_bot.modules.ai.filters.quota.check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    )
    stack.enter_context(patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)))


# ---------------------------------------------------------------------------
# /aiusage tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_summaries_shows_status(test_client: TestClient) -> None:
    """Admin calling /ai_summaries without args should see the current summary status."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000041, title="AISummaries Status Group")
    admin_wrapper = test_client.create_user(user_id=929000041, first_name="AdminSummaries", username="admin_summaries")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        requests = await test_client.send_command(
            command="ai_summaries",
            from_user=admin_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to admin /ai_summaries"
    response_text = requests[-1].text or ""
    assert "Current state" in response_text or "AI Chat Summaries" in response_text, (
        f"Expected status display, got: {response_text}"
    )


@pytest.mark.asyncio
async def test_ai_note_titles_shows_status(test_client: TestClient) -> None:
    """Admin calling /ai_note_titles without args should see the current note title status."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000042, title="AINoteTitles Status Group")
    admin_wrapper = test_client.create_user(user_id=929000042, first_name="AdminTitles", username="admin_titles")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        requests = await test_client.send_command(
            command="ai_note_titles",
            from_user=admin_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to admin /ai_note_titles"
    response_text = requests[-1].text or ""
    assert "Current state" in response_text or "AI Note Titles" in response_text, (
        f"Expected status display, got: {response_text}"
    )


@pytest.mark.asyncio
async def test_aiusage_shows_usage_info(test_client: TestClient) -> None:
    """The /aiusage command should display quota and usage breakdown."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000005, title="AIUsage Info Group")
    user_wrapper = test_client.create_user(user_id=929000005, first_name="UsageUser", username="usage_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    mock_usage_view = ChatUsageView(
        total_credits=10000,
        used_credits=500,
        remaining_credits=9500,
        percentage_remaining=95,
        period_end=date(2026, 6, 30),
        breakdown=(
            ChatUsageBreakdownItem(
                feature=AI_FEATURE_CHATBOT,
                title="Chatbot",
                icon="\ud83d\udcac",
                credits=300,
                percentage=60,
            ),
            ChatUsageBreakdownItem(
                feature=AI_FEATURE_TRANSLATE,
                title="Translate",
                icon="\ud83c\udf10",
                credits=200,
                percentage=40,
            ),
        ),
    )

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.usage.get_chat_usage_view",
                AsyncMock(return_value=mock_usage_view),
            )
        )
        requests = await test_client.send_command(
            command="aiusage",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /aiusage"
    response_text = requests[-1].text or ""
    assert "AI Usage" in response_text, f"Expected 'AI Usage' header, got: {response_text}"
    assert "9,500" in response_text or "9500" in response_text, (
        f"Expected remaining credits in response, got: {response_text}"
    )
    assert "95" in response_text, f"Expected percentage remaining, got: {response_text}"


@pytest.mark.asyncio
async def test_aiusage_not_available(test_client: TestClient) -> None:
    """The /aiusage command shows 'not available' when no quota data exists."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000006, title="AIUsage NoData Group")
    user_wrapper = test_client.create_user(user_id=929000006, first_name="NoDataUser", username="nodata_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, user_wrapper.user.id)

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.usage.get_chat_usage_view",
                AsyncMock(return_value=None),
            )
        )
        requests = await test_client.send_command(
            command="aiusage",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /aiusage even without data"
    response_text = requests[-1].text or ""
    assert "not available" in response_text.lower(), f"Expected 'not available' message, got: {response_text}"


# ---------------------------------------------------------------------------
# /aireset tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aireset_success(test_client: TestClient) -> None:
    """Admin calling /aireset should reset context and show a success message."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000007, title="AIReset Success Group")
    admin_wrapper = test_client.create_user(user_id=929000007, first_name="AdminReset", username="admin_reset")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    mock_reset_messages = AsyncMock()
    mock_clear = AsyncMock()

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        stack.enter_context(patch("sophie_bot.modules.ai.handlers.reset_context.reset_messages", mock_reset_messages))
        stack.enter_context(patch("sophie_bot.modules.ai.handlers.reset_context.AIMemoryModel.clear", mock_clear))
        requests = await test_client.send_command(
            command="aireset",
            from_user=admin_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /aireset"
    response_text = requests[-1].text or ""
    assert "reset" in response_text.lower() or "clean" in response_text.lower(), (
        f"Expected reset success message, got: {response_text}"
    )
    mock_reset_messages.assert_awaited_once()
    mock_clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_aireset_requires_admin(test_client: TestClient) -> None:
    """Non-admin users should not be able to reset AI context."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000008, title="AIReset Deny Group")
    user_wrapper = test_client.create_user(user_id=929000008, first_name="RegReset", username="reg_reset")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    with ExitStack() as stack:
        _apply_ai_non_admin_patches(stack)
        requests = await test_client.send_command(
            command="aireset",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to non-admin /aireset attempt"
    response_text = requests[-1].text or ""
    assert "administrator" in response_text.lower() or "admin" in response_text.lower(), (
        f"Expected permission error, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# /aimode tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aimode_shows_mode_picker(test_client: TestClient) -> None:
    """Admin calling /aimode should see the mode table with an inline keyboard."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000009, title="AIMode Group")
    admin_wrapper = test_client.create_user(user_id=929000009, first_name="AdminMode", username="admin_mode")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        requests = await test_client.send_command(
            command="aimode",
            from_user=admin_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /aimode"
    last_request = requests[-1]
    # The picker is sent as a rich message, so the mode table is in the rich payload, not in `text`.
    rendered = str(last_request.params.get("rich_message") or last_request.text or "")
    assert "Mode" in rendered, f"Expected the mode table, got: {rendered}"
    keyboard = last_request.reply_markup
    assert keyboard is not None, "Expected inline keyboard with mode options"
    assert len(keyboard["inline_keyboard"]) == 4, "Expected one button per mode"


# ---------------------------------------------------------------------------
# /aitranslate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translate_success(test_client: TestClient) -> None:
    """The /translate command should return a translated text."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000010, title="Translate Success Group")
    user_wrapper = test_client.create_user(user_id=929000010, first_name="TransUser", username="trans_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    mock_ai_result = SimpleNamespace(
        output=SimpleNamespace(
            translated_text="Hola mundo",
            origin_language_name="English",
            origin_language_emoji="\ud83c\uddec\ud83c\udde7",
            needs_translation=True,
            translation_explanations=None,
        ),
        usage=SimpleNamespace(total_tokens=100, request_tokens=50, response_tokens=50),
    )

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.translate.run_structured_task",
                AsyncMock(return_value=mock_ai_result),
            )
        )
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.translate.get_chat_translations_model",
                AsyncMock(return_value=SimpleNamespace(model_name="test-model")),
            )
        )
        requests = await test_client.send_command(
            command="translate",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="Hello world",
        )

    assert requests, "Bot should respond to /translate"
    response_text = requests[-1].text or ""
    assert "Hola mundo" in response_text, f"Expected translated text in response, got: {response_text}"
    assert "English" in response_text or "\ud83c\uddec\ud83c\udde7" in response_text, (
        f"Expected origin language info in response, got: {response_text}"
    )


@pytest.mark.asyncio
async def test_translate_empty_text_error(test_client: TestClient) -> None:
    """The /translate command without text should return an error message."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000011, title="Translate Empty Group")
    user_wrapper = test_client.create_user(user_id=929000011, first_name="EmptyTransUser", username="empty_trans")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, user_wrapper.user.id)

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.translate.get_chat_translations_model",
                AsyncMock(return_value=SimpleNamespace(model_name="test-model")),
            )
        )
        requests = await test_client.send_command(
            command="translate",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /translate without text"
    response_text = requests[-1].text or ""
    assert "provide" in response_text.lower() or "text" in response_text.lower(), (
        f"Expected error about missing text, got: {response_text}"
    )


@pytest.mark.asyncio
async def test_translate_ai_failure(test_client: TestClient) -> None:
    """The /translate command should show a graceful error when AI fails."""
    group_chat = ChatFactory.create_group(chat_id=-1002900000012, title="Translate Failure Group")
    user_wrapper = test_client.create_user(user_id=929000012, first_name="FailTransUser", username="fail_trans")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, user_wrapper.user.id)

    with ExitStack() as stack:
        _apply_ai_admin_patches(stack)
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.translate.run_structured_task",
                AsyncMock(side_effect=AIRequestFailed("fake-sentry-id")),
            )
        )
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.translate.get_chat_translations_model",
                AsyncMock(return_value=SimpleNamespace(model_name="test-model")),
            )
        )

        requests = await test_client.send_command(
            command="translate",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="Hello world",
        )

    assert requests, "Bot should respond with an error when AI fails"
    response_text = requests[-1].text or ""
    assert "AI provider did not complete" in response_text, f"Expected AI failure message, got: {response_text}"
