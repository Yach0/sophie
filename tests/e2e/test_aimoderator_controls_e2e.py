from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

import pytest
from aiogram import Bot
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory
from aiogram_test_framework.mock_bot import MockSession
from aiogram_test_framework.types import CapturedRequest, RequestType

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.modules.ai.api import moderator as moderator_api
from sophie_bot.modules.ai.api.schemas import ModeratorSettingsUpdate
from sophie_bot.modules.ai.callbacks import AIModeratorCategoryCallback
from sophie_bot.modules.ai.utils.moderation.categories import MODERATION_CATEGORIES_TITLES, ModerationCategory
from sophie_bot.modules.ai.utils.moderation.settings import get_moderator_settings, set_category_level
from tests.e2e.helpers import create_test_user_and_group, grant_admin, set_feature
from tests.utils.rich_html import parse_rich_html


def _table(requests: list[CapturedRequest]) -> ElementTree.Element:
    request = next(request for request in reversed(requests) if request.params.get("rich_message"))
    assert not request.params.get("reply_markup"), "All controls must be embedded in the table"
    root = parse_rich_html(request.params["rich_message"]["html"])
    table = root.find("table")
    assert table is not None
    assert len(root.findall(".//tg-button")) == len(table.findall(".//tg-button"))
    assert all(len(row) == 2 for row in table.findall("tr")), "Keep the table narrow"
    return table


def _assert_control(table: ElementTree.Element, label: str, callback: str, value: str) -> None:
    row = next(row for row in table.findall("tr") if label in "".join(row[0].itertext()))
    button = row[1].find("tg-button")
    assert button is not None, "The value itself must be an inline control in the corresponding row"
    assert button.attrib == {"type": "callback_data", "data": callback}
    assert value in "".join(button.itertext())


async def test_picker_renders_enabled_and_each_category_control_in_its_row(test_client: TestClient) -> None:
    admin, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    await AIModeModel.set_mode(chat, AIMode.support)
    # Isolate the external classifier; no paid provider requests.
    with patch(
        "sophie_bot.modules.ai.middlewares.ai_moderator.check_moderator",
        AsyncMock(return_value=SimpleNamespace(flagged=False)),
    ):
        requests = await test_client.send_command(command="aimoderator", from_user=admin, chat=group)
    table = _table(requests)
    _assert_control(table, "Enabled", "ai_mod_enabled", "On")
    for category in ModerationCategory:
        _assert_control(
            table,
            str(MODERATION_CATEGORIES_TITLES[category]),
            AIModeratorCategoryCallback(category=category.value).pack(),
            "Medium",
        )
    assert len(table.findall(".//tg-button")) == len(ModerationCategory) + 1
    assert await get_moderator_settings(chat.iid) is None


@pytest.mark.parametrize("states", [("off", "on", "off"), ("on", "off", "on")])
async def test_real_ass_on_off_arguments_persist_and_no_args_preserves_state(
    test_client: TestClient, states: tuple[str, ...]
) -> None:
    admin, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    for state in states:
        requests = await test_client.send_command(command="aimoderator", args=state, from_user=admin, chat=group)
        settings = await get_moderator_settings(chat.iid)
        assert settings is not None
        assert settings.enabled is (state == "on")
        _assert_control(_table(requests), "Enabled", "ai_mod_enabled", "On" if state == "on" else "Off")
        await test_client.send_command(command="aimoderator", from_user=admin, chat=group)
        settings = await get_moderator_settings(chat.iid)
        assert settings is not None and settings.enabled is (state == "on")


async def test_invalid_ass_argument_does_not_show_picker_or_write_settings(test_client: TestClient) -> None:
    admin, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    requests = await test_client.send_command(command="aimoderator", args="maybe", from_user=admin, chat=group)
    assert requests
    assert not any(request.params.get("rich_message") for request in requests)
    assert await get_moderator_settings(chat.iid) is None


async def test_enabled_callback_overrides_entertainment_default_and_edits_inline_value(test_client: TestClient) -> None:
    admin, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    requests = await test_client.send_command(command="aimoderator", from_user=admin, chat=group)
    _assert_control(_table(requests), "Enabled", "ai_mod_enabled", "Off")
    picker = MessageFactory.create(
        text="AI Moderator", from_user=UserFactory.create(user_id=CONFIG.bot_id, is_bot=True), chat=group
    )
    for value, enabled in (("On", True), ("Off", False)):
        requests = await test_client.send_callback("ai_mod_enabled", from_user=admin, message=picker)
        settings = await get_moderator_settings(chat.iid)
        assert settings is not None and settings.enabled is enabled
        assert any(request.request_type == RequestType.EDIT_MESSAGE_TEXT for request in requests)
        _assert_control(_table(requests), "Enabled", "ai_mod_enabled", value)


async def test_category_callback_updates_its_inline_value_without_enabling_entertainment(
    test_client: TestClient,
) -> None:
    admin, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    picker = MessageFactory.create(
        text="AI Moderator", from_user=UserFactory.create(user_id=CONFIG.bot_id, is_bot=True), chat=group
    )
    callback = AIModeratorCategoryCallback(category=ModerationCategory.PII.value).pack()
    requests = await test_client.send_callback(callback, from_user=admin, message=picker)
    table = _table(requests)
    _assert_control(table, "Personal data", callback, "High")
    _assert_control(table, "Enabled", "ai_mod_enabled", "Off")
    settings = await get_moderator_settings(chat.iid)
    assert settings is not None and settings.enabled is None


@pytest.mark.parametrize("interaction", ["command", "callback"])
async def test_non_admin_cannot_change_enabled(test_client: TestClient, interaction: str) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    if interaction == "command":
        await test_client.send_command(command="aimoderator", args="on", from_user=user, chat=group)
    else:
        picker = MessageFactory.create(
            text="AI Moderator", from_user=UserFactory.create(user_id=CONFIG.bot_id, is_bot=True), chat=group
        )
        await test_client.send_callback("ai_mod_enabled", from_user=user, message=picker)
    assert await get_moderator_settings(chat.iid) is None


@pytest.mark.parametrize("interaction", ["command", "picker", "enabled_callback", "category_callback"])
async def test_moderation_feature_flag_disables_controls(test_client: TestClient, interaction: str) -> None:
    admin, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    if interaction in {"command", "picker"}:
        requests = await test_client.send_command(
            command="aimoderator", args="on" if interaction == "command" else "", from_user=admin, chat=group
        )
    else:
        picker = MessageFactory.create(
            text="AI Moderator", from_user=UserFactory.create(user_id=CONFIG.bot_id, is_bot=True), chat=group
        )
        callback = (
            "ai_mod_enabled"
            if interaction == "enabled_callback"
            else AIModeratorCategoryCallback(category=ModerationCategory.PII.value).pack()
        )
        requests = await test_client.send_callback(callback, from_user=admin, message=picker)
    assert not any(request.params.get("rich_message") for request in requests)
    assert await get_moderator_settings(chat.iid) is None


@pytest.mark.parametrize(
    "mode,enabled",
    [(AIMode.support, False), (AIMode.entertainment, None), (AIMode.entertainment, False)],
)
async def test_disabled_moderator_skips_provider_and_tr_still_translates(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch, mode: AIMode, enabled: bool | None
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, mode)
    if enabled is not None:
        await AIModeratorModel(chat=chat, enabled=enabled).save()
    await set_feature(test_client, "ai_translations", True, chat_tid=group.id)
    session = cast(MockSession, test_client.bot.session)
    original_response = session._generate_response

    def rich_response(bot: Bot, method_name: str, params: dict[str, object]) -> object:
        return original_response(
            bot=bot, method_name="sendMessage" if method_name == "sendRichMessage" else method_name, params=params
        )

    monkeypatch.setattr(session, "_generate_response", rich_response)
    provider = AsyncMock(side_effect=AssertionError("Disabled moderator must not select a provider"))
    translation = AsyncMock(
        return_value=SimpleNamespace(
            output=SimpleNamespace(
                translated_text="Hola mundo",
                origin_language_name="English",
                origin_language_emoji="🇬🇧",
                needs_translation=True,
                translation_explanations=None,
            )
        )
    )
    with (
        patch("sophie_bot.modules.ai.utils.moderation.get_moderation_provider", provider),
        patch("sophie_bot.modules.ai.handlers.translate.run_structured_task", translation),
        patch(
            "sophie_bot.modules.ai.handlers.translate.get_chat_translations_model_plan",
            AsyncMock(return_value=SimpleNamespace(model_name="test-model")),
        ),
        patch("sophie_bot.modules.ai.filters.quota.check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True))),
        patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)),
    ):
        requests = await test_client.send_command(command="tr", args="Hello world", from_user=user, chat=group)
    provider.assert_not_awaited()
    translation.assert_awaited_once()
    assert any("Hola mundo" in request.params.get("rich_message", {}).get("html", "") for request in requests)


@pytest.mark.parametrize("enabled", [False, True])
async def test_explicit_override_survives_mode_changes_and_category_edits(
    test_client: TestClient, enabled: bool
) -> None:
    admin, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    await test_client.send_command(command="aimoderator", args="on" if enabled else "off", from_user=admin, chat=group)
    for mode in (AIMode.entertainment, AIMode.support, AIMode.moderation):
        await AIModeModel.set_mode(chat, mode)
        response = await moderator_api.update_moderator_settings(
            chat.iid, ModeratorSettingsUpdate(pii=DetectionLevel.HIGH), _user_model
        )
        assert response.enabled is enabled
        settings = await get_moderator_settings(chat.iid)
        assert settings is not None and settings.enabled is enabled
        assert settings.pii == DetectionLevel.HIGH


@pytest.mark.parametrize("mode,expected", [(AIMode.support, True), (AIMode.entertainment, False)])
async def test_rest_settings_use_mode_default_after_category_only_update(
    test_client: TestClient, mode: AIMode, expected: bool
) -> None:
    _user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, mode)
    before = await moderator_api.get_moderator_settings(chat.iid, _user_model)
    assert before.enabled is expected
    after = await moderator_api.update_moderator_settings(
        chat.iid, ModeratorSettingsUpdate(pii=DetectionLevel.HIGH), _user_model
    )
    assert after.enabled is expected
    settings = await get_moderator_settings(chat.iid)
    assert settings is not None and settings.enabled is None


@pytest.mark.parametrize(
    "callback", ["ai_mod_enabled", AIModeratorCategoryCallback(category=ModerationCategory.PII.value).pack()]
)
@pytest.mark.parametrize("is_target_admin", [False, True])
async def test_connected_pm_callbacks_require_target_chat_admin(
    test_client: TestClient, callback: str, is_target_admin: bool
) -> None:
    user, group, user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    if is_target_admin:
        await grant_admin(group.id, user.id)
    await test_client.send_command(command="connect", from_user=user, args=str(group.id))
    connection = await ChatConnectionModel.get_by_user_tid(user.id)
    assert connection is not None and connection.chat is not None
    assert connection.chat.to_ref().id == chat.iid
    picker = MessageFactory.create(
        text="AI Moderator",
        from_user=UserFactory.create(user_id=CONFIG.bot_id, is_bot=True),
        chat=ChatFactory.create_private(chat_id=user.id),
    )

    requests = await test_client.send_callback(callback, from_user=user, message=picker)

    answers = [request for request in requests if request.request_type == RequestType.ANSWER_CALLBACK_QUERY]
    assert answers
    settings = await get_moderator_settings(chat.iid)
    if is_target_admin:
        assert settings is not None
        if callback == "ai_mod_enabled":
            assert settings.enabled is True
        else:
            assert settings.pii == DetectionLevel.HIGH
        assert any(request.request_type == RequestType.EDIT_MESSAGE_TEXT for request in requests)
    else:
        assert settings is None
        assert any("not allowed" in request.params.get("text", "") for request in answers)
        assert not any(request.request_type == RequestType.EDIT_MESSAGE_TEXT for request in requests)
    assert await get_moderator_settings(user_model.iid) is None


@pytest.mark.parametrize("writer", ["category", "rest_category", "enabled", "rest_enabled"])
@pytest.mark.parametrize("new_state", [False, True])
async def test_interleaved_settings_writes_preserve_other_fields(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch, writer: str, new_state: bool
) -> None:
    _user, group, user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    settings = await AIModeratorModel(chat=chat, enabled=not new_state).insert()
    collection = AIModeratorModel.get_pymongo_collection()
    original_update = collection.find_one_and_update
    concurrent_values = {"pii": "high"} if writer in {"enabled", "rest_enabled"} else {"enabled": new_state}
    interleaved = False

    async def interleave(*args: object, **kwargs: object) -> object:
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            await collection.update_one({"_id": settings.id}, {"$set": concurrent_values})
        return await original_update(*args, **kwargs)

    monkeypatch.setattr(collection, "find_one_and_update", interleave)
    if writer == "enabled":
        await AIModeratorModel.set_state(chat, new_state)
    elif writer == "rest_enabled":
        await moderator_api.update_moderator_settings(chat.iid, ModeratorSettingsUpdate(enabled=new_state), user_model)
    elif writer == "rest_category":
        await moderator_api.update_moderator_settings(
            chat.iid, ModeratorSettingsUpdate(pii=DetectionLevel.HIGH), user_model
        )
    else:
        await set_category_level(chat, ModerationCategory.PII, DetectionLevel.HIGH)
    stored = await get_moderator_settings(chat.iid)
    assert interleaved
    assert stored is not None and stored.enabled is new_state
    assert stored.pii == DetectionLevel.HIGH


@pytest.mark.parametrize("writer", ["category", "rest_category", "enabled", "rest_enabled"])
async def test_first_write_race_keeps_one_record_and_both_updates(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch, writer: str
) -> None:
    _user, group, user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    collection = AIModeratorModel.get_pymongo_collection()
    await collection.create_indexes(AIModeratorModel.Settings.indexes)
    original_insert = collection.insert_one
    raced = False

    async def competing_insert(*args: object, **kwargs: object) -> object:
        nonlocal raced
        if not raced:
            raced = True
            competing = AIModeratorModel(
                chat=chat,
                enabled=True,
                pii=DetectionLevel.HIGH if writer in {"enabled", "rest_enabled"} else DetectionLevel.NORMAL,
            )
            await competing.insert()
        return await original_insert(*args, **kwargs)

    monkeypatch.setattr(collection, "insert_one", competing_insert)
    try:
        if writer == "enabled":
            await AIModeratorModel.set_state(chat, False)
        elif writer == "rest_enabled":
            await moderator_api.update_moderator_settings(chat.iid, ModeratorSettingsUpdate(enabled=False), user_model)
        elif writer == "rest_category":
            await moderator_api.update_moderator_settings(
                chat.iid, ModeratorSettingsUpdate(pii=DetectionLevel.HIGH), user_model
            )
        else:
            await set_category_level(chat, ModerationCategory.PII, DetectionLevel.HIGH)
        assert raced
        assert await AIModeratorModel.find(AIModeratorModel.chat.id == chat.iid).count() == 1
        stored = await get_moderator_settings(chat.iid)
        assert stored is not None and stored.enabled is (writer not in {"enabled", "rest_enabled"})
        assert stored.pii == DetectionLevel.HIGH
    finally:
        await collection.drop_index("moderator_chat_unique")
