from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework.factories import MessageFactory, UserFactory

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.filters.message_status import HasArgs
from sophie_bot.modules.ai.handlers import aimoderator
from sophie_bot.modules.ai.middlewares.ai_moderator import AiModeratorMiddleware
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.services.application import ApplicationServices
from tests.utils.rich_html import parse_rich_html


async def test_hasargs_selects_separate_control_handler() -> None:
    control = getattr(aimoderator, "AIModeratorControl", None)
    assert control is not None
    for args, expected in (("", False), (" on", True), (" off", True)):
        message = MessageFactory.create(from_user=UserFactory.create(user_id=42), text="/aimoderator" + args)
        control_filter_candidates = (filter_ for filter_ in control.filters() if isinstance(filter_, HasArgs))
        control_filter = next(control_filter_candidates)
        picker_filter = aimoderator.AIModeratorSetting.filters()[-1]
        assert await control_filter(message) is expected
        assert await picker_filter(message) is (not expected)


@pytest.mark.parametrize(
    "mode,enabled,expected",
    [
        (AIMode.support, None, True),
        (AIMode.moderation, None, True),
        (AIMode.entertainment, None, False),
        (AIMode.entertainment, True, True),
        (AIMode.support, False, False),
        (AIMode.moderation, False, False),
        (AIMode.disabled, True, False),
    ],
)
async def test_middleware_effective_enabled_controls_classification(
    mode: AIMode, enabled: bool | None, expected: bool, test_services: ApplicationServices
) -> None:
    message = MessageFactory.create(from_user=UserFactory.create(user_id=42), text="hello")
    chat = SimpleNamespace(tid=message.chat.id, iid="chat-iid", type="group")
    settings = SimpleNamespace(enabled=enabled) if enabled is not None else None
    data = {
        "services": test_services,
        "context": SimpleNamespace(event_chat=chat),
        "ai_mode": mode,
        "ai_capabilities": get_capabilities(mode),
    }
    handler = AsyncMock(return_value="handled")
    classifier = AsyncMock(return_value=SimpleNamespace(flagged=False))
    with (
        patch(
            "sophie_bot.modules.ai.middlewares.ai_moderator.AIModeratorModel.find_one", AsyncMock(return_value=settings)
        ),
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_enabled", AsyncMock(return_value=True)),
        patch(
            "sophie_bot.modules.ai.middlewares.ai_moderator.is_user_group_whitelisted", AsyncMock(return_value=False)
        ),
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_user_admin", AsyncMock(return_value=False)),
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.check_moderator", classifier),
    ):
        assert await AiModeratorMiddleware()(handler, message, data) == "handled"
    assert classifier.await_count == int(expected)
    handler.assert_awaited_once_with(message, data)


def test_picker_uses_short_descriptions_with_native_paragraphs_for_mobile() -> None:
    table = parse_rich_html(aimoderator._build_doc(None, False).to_rich()).find("table")
    assert table is not None
    assert "compact" in table.attrib
    for row in table.findall("tr")[2:]:
        description = row[0].find("p")
        assert description is not None
        assert len("".join(description.itertext())) <= 65


@pytest.mark.parametrize(
    "mode,enabled,expected",
    [
        (AIMode.entertainment, None, False),
        (AIMode.entertainment, False, False),
        (AIMode.entertainment, True, True),
        (AIMode.support, False, False),
        (AIMode.moderation, False, False),
        (AIMode.disabled, True, False),
    ],
)
@pytest.mark.parametrize("feature_enabled", [False, True])
async def test_runtime_provider_calls_follow_enabled_override(
    mode: AIMode, enabled: bool | None, expected: bool, test_services: ApplicationServices, feature_enabled: bool
) -> None:
    message = MessageFactory.create(from_user=UserFactory.create(user_id=42), text="hello")
    expected = expected and feature_enabled
    settings = SimpleNamespace(enabled=enabled)
    data = {
        "services": test_services,
        "context": SimpleNamespace(event_chat=SimpleNamespace(tid=message.chat.id, iid="chat-iid", type="group")),
        "ai_mode": mode,
        "ai_capabilities": get_capabilities(mode),
    }
    classifier = AsyncMock(return_value={})
    provider = AsyncMock(return_value=SimpleNamespace(classify=classifier))
    handler = AsyncMock(return_value="handled")
    with (
        patch(
            "sophie_bot.modules.ai.middlewares.ai_moderator.AIModeratorModel.find_one", AsyncMock(return_value=settings)
        ),
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_enabled", return_value=feature_enabled),
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_user_group_whitelisted", return_value=False),
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_user_admin", return_value=False),
        patch("sophie_bot.modules.ai.utils.moderation.get_moderation_provider", provider),
    ):
        assert await AiModeratorMiddleware()(handler, message, data) == "handled"
    assert provider.await_count == int(expected)
    assert classifier.await_count == int(expected)
    handler.assert_awaited_once_with(message, data)


async def test_update_fields_unsets_none_and_omits_null_enabled(db_init: Any) -> None:
    chat = ChatModel(
        tid=-1001234567899,
        type=ChatType.supergroup,
        first_name_or_title="Test Moderator Group",
        username="test_moderator_group",
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await chat.insert()
    chat = await ChatModel.get(chat.id)
    assert chat is not None

    collection = db_init["ai_moderator"]
    await collection.delete_many({})

    # 1. Update only category levels on a new chat - enabled must not be stored as null
    created = await AIModeratorModel.update_fields(chat, {"pii": DetectionLevel.HIGH})
    raw_created = await collection.find_one({"_id": created.id})
    assert raw_created is not None
    assert "enabled" not in raw_created
    assert created.enabled is None
    assert created.pii == DetectionLevel.HIGH

    # 2. Explicit enable stores True
    enabled_doc = await AIModeratorModel.set_state(chat, True)
    raw_enabled = await collection.find_one({"_id": enabled_doc.id})
    assert raw_enabled is not None
    assert raw_enabled["enabled"] is True
    assert enabled_doc.enabled is True

    # 3. Explicit disable stores False
    disabled_doc = await AIModeratorModel.set_state(chat, False)
    raw_disabled = await collection.find_one({"_id": disabled_doc.id})
    assert raw_disabled is not None
    assert raw_disabled["enabled"] is False
    assert disabled_doc.enabled is False

    # 4. Reset to None unsets enabled in MongoDB rather than writing null
    reset_doc = await AIModeratorModel.update_fields(chat, {"enabled": None})
    raw_reset = await collection.find_one({"_id": reset_doc.id})
    assert raw_reset is not None
    assert "enabled" not in raw_reset
    assert reset_doc.enabled is None

    await collection.delete_many({})
    await chat.delete()
