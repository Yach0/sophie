from __future__ import annotations

from datetime import timedelta

import pytest
from aiogram.types import Chat, Message, PhotoSize, Update, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, GreetingsModel, WSUserModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.db.models.greetings import WelcomeMute, WelcomeSecurity
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_message_id, next_user_id, set_feature


async def _panel_group(test_client: TestClient) -> tuple[User, Chat, ChatModel, Message]:
    admin, group, _ = await create_test_user_and_group(test_client)
    await grant_admin(group.id, admin.id)
    await set_feature(test_client, "welcome_security_settings_panel", True, chat_tid=group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    message = MessageFactory.create(
        text="Welcome Security",
        from_user=UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True),
        chat=group,
    )
    return admin, group, chat, message


async def _click(
    test_client: TestClient, admin: User, chat: ChatModel, message: Message, action: str, value: int = 0
) -> list:
    callback = WelcomeSecuritySettingsCB(chat_iid=str(chat.iid), action=action, value=value).pack()
    assert len(callback.encode()) <= 64
    return await test_client.send_callback(callback, from_user=admin, message=message)


@pytest.mark.parametrize("command", ["welcomesecurity", "welcomecaptcha", "enablewelcomecaptcha", "welcomerestrict"])
async def test_settings_commands_open_flagged_panel(test_client: TestClient, command: str) -> None:
    admin, group, chat, _ = await _panel_group(test_client)
    requests = await test_client.send_command(command=command, from_user=admin, chat=group)
    sent = next(request for request in requests if request.request_type == RequestType.SEND_MESSAGE)
    assert "Unanswered CAPTCHA" in sent.text and "Media restriction" in sent.text
    keyboard = sent.params["reply_markup"]
    if not isinstance(keyboard, dict):
        keyboard = keyboard.model_dump()
    callbacks = [button["callback_data"] for row in keyboard["inline_keyboard"] for button in row]
    assert all(WelcomeSecuritySettingsCB.unpack(callback).chat_iid == str(chat.iid) for callback in callbacks)
    assert all(len(callback.encode()) <= 64 for callback in callbacks)
    assert await GreetingsModel.find_one(GreetingsModel.chat.id == chat.iid) is None


async def test_settings_update_durations_without_enabling_features_or_changing_pending_users(
    test_client: TestClient,
) -> None:
    admin, _group, chat, message = await _panel_group(test_client)
    user = await ChatModel.get_by_tid(admin.id)
    assert user is not None
    pending = await WSUserModel.ensure_user(user, chat, is_join_request=True)
    original = pending.model_dump(exclude={"user", "group"})
    await _click(test_client, admin, chat, message, "set_expiry", 43200)
    await _click(test_client, admin, chat, message, "set_duration", 3600)
    settings = await GreetingsModel.get_by_chat_iid(chat.iid)
    assert settings.welcome_security == WelcomeSecurity(enabled=False, expire=timedelta(hours=12))
    assert settings.welcome_mute == WelcomeMute(enabled=False, time=timedelta(hours=1))
    await _click(test_client, admin, chat, message, "captcha", 1)
    await _click(test_client, admin, chat, message, "media", 1)
    await _click(test_client, admin, chat, message, "cleanup", 1)
    await _click(test_client, admin, chat, message, "welcome_cleanup", 1)
    settings = await GreetingsModel.get_by_chat_iid(chat.iid)
    assert settings.welcome_security.enabled and settings.welcome_security.expire == timedelta(hours=12)
    assert settings.welcome_mute.enabled and settings.welcome_mute.time == timedelta(hours=1)
    assert settings.clean_service.enabled and settings.clean_welcome.enabled
    assert (await WSUserModel.get(pending.id)).model_dump(exclude={"user", "group"}) == original


async def test_settings_show_effective_service_flags_and_pending_counts(test_client: TestClient) -> None:
    admin, group, chat, message = await _panel_group(test_client)
    settings = await GreetingsModel.get_by_chat_iid(chat.iid)
    await settings.set_status_welcomesecurity(True)
    await set_feature(test_client, "welcomecaptcha", False, chat_tid=group.id)
    await set_feature(test_client, "welcomecaptcha_autokick", False, chat_tid=group.id)
    requests = await test_client.send_command(command="welcomesecurity", from_user=admin, chat=group)
    text = "\n".join(request.text or "" for request in requests)
    assert "Disabled by service configuration" in text
    assert "Timeout removal disabled by service configuration" in text
    user = await ChatModel.get_by_tid(admin.id)
    assert user is not None
    pending = await WSUserModel.ensure_user(user, chat, is_join_request=False)
    pending.passed = True
    await pending.save()
    requests = await _click(test_client, admin, chat, message, "pending")
    text = "\n".join(request.text or "" for request in requests)
    assert "Verified CAPTCHA records" in text
    assert await WSUserModel.get(pending.id) is not None
    assert not any(request.request_type == RequestType.RESTRICT_CHAT_MEMBER for request in requests)


@pytest.mark.parametrize(
    "action,value",
    [("captcha", 2), ("media", -1), ("set_expiry", 1), ("set_duration", 1), ("cleanup", 2), ("unknown", 0)],
)
async def test_settings_reject_invalid_values(test_client: TestClient, action: str, value: int) -> None:
    admin, _group, chat, message = await _panel_group(test_client)
    requests = await _click(test_client, admin, chat, message, action, value)
    assert any(request.request_type == RequestType.ANSWER_CALLBACK_QUERY for request in requests)
    assert await GreetingsModel.find_one(GreetingsModel.chat.id == chat.iid) is None


@pytest.mark.parametrize(
    "action",
    [
        "captcha",
        "media",
        "set_expiry",
        "set_duration",
        "cleanup",
        "welcome_cleanup",
        "edit",
        "reset",
        "pending",
        "message",
    ],
)
async def test_settings_callbacks_require_current_administrator(test_client: TestClient, action: str) -> None:
    _admin, _group, chat, message = await _panel_group(test_client)
    stranger = User(id=next_user_id(), is_bot=False, first_name="Stranger")
    await ChatModel.upsert_user(stranger)
    requests = await _click(test_client, stranger, chat, message, action, 1)
    assert not any(request.request_type == RequestType.EDIT_MESSAGE_TEXT for request in requests)
    assert await GreetingsModel.find_one(GreetingsModel.chat.id == chat.iid) is None


@pytest.mark.parametrize("change", ["disconnect", "switch"])
async def test_settings_callbacks_are_bound_to_connected_group(test_client: TestClient, change: str) -> None:
    admin, _group, chat, _ = await _panel_group(test_client)
    chat.username = "panel_target"
    await chat.save()
    await test_client.send_command(command="connect", from_user=admin, args="@panel_target")
    message = MessageFactory.create(text="Settings", chat=Chat(id=admin.id, type="private"), from_user=admin)
    if change == "disconnect":
        await test_client.send_command(command="disconnect", from_user=admin)
    else:
        _other_admin, other_group, _ = await create_test_user_and_group(test_client)
        await grant_admin(other_group.id, admin.id)
        other = await ChatModel.get_by_tid(other_group.id)
        assert other is not None
        other.username = "other_panel_target"
        await other.save()
        await set_feature(test_client, "welcome_security_settings_panel", True, chat_tid=other.tid)
        await test_client.send_command(command="connect", from_user=admin, args="@other_panel_target")
    requests = await _click(test_client, admin, chat, message, "captcha", 1)
    assert not any(request.request_type == RequestType.EDIT_MESSAGE_TEXT for request in requests)
    assert await GreetingsModel.find_one(GreetingsModel.chat.id == chat.iid) is None


@pytest.mark.parametrize("with_photo", [False, True])
async def test_security_message_edit_view_and_confirmed_reset(test_client: TestClient, with_photo: bool) -> None:
    admin, group, chat, message = await _panel_group(test_client)
    await _click(test_client, admin, chat, message, "edit")
    state = test_client.dispatcher.fsm.get_context(bot=test_client.bot, chat_id=group.id, user_id=admin.id)
    data = await state.get_data()
    prompt = MessageFactory.create(
        message_id=data["ws_settings_prompt_id"], text="New security message", chat=group, from_user=admin
    )
    response = MessageFactory.create(text="Be kind to everyone", from_user=admin, chat=group, reply_to_message=prompt)
    if with_photo:
        response = response.model_copy(
            update={
                "text": None,
                "caption": "Be kind to everyone",
                "photo": [PhotoSize(file_id="security-photo", file_unique_id="security-photo", width=100, height=100)],
            }
        )
    await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=response))
    settings = await GreetingsModel.get_by_chat_iid(chat.iid)
    assert settings.security_note is not None and settings.security_note.text == "Be kind to everyone"
    assert bool(settings.security_note.file) == with_photo
    assert await state.get_state() is None
    requests = await _click(test_client, admin, chat, message, "message")
    assert any("Be kind to everyone" in (request.text or request.params.get("caption", "")) for request in requests)
    await _click(test_client, admin, chat, message, "reset_confirm")
    assert (await GreetingsModel.get_by_chat_iid(chat.iid)).security_note is not None
    await _click(test_client, admin, chat, message, "reset")
    assert (await GreetingsModel.get_by_chat_iid(chat.iid)).security_note is None


async def test_panel_flag_preserves_legacy_commands_and_blocks_old_buttons(test_client: TestClient) -> None:
    admin, group, chat, message = await _panel_group(test_client)
    await test_client.send_command(command="welcomecaptcha", args="6h", from_user=admin, chat=group)
    assert (await GreetingsModel.get_by_chat_iid(chat.iid)).welcome_security.expire == timedelta(hours=6)
    await set_feature(test_client, "welcome_security_settings_panel", False, chat_tid=group.id)
    requests = await _click(test_client, admin, chat, message, "captcha", 0)
    assert any("disabled" in request.params.get("text", "").lower() for request in requests)
    assert (await GreetingsModel.get_by_chat_iid(chat.iid)).welcome_security.enabled
    requests = await test_client.send_command(command="welcomesecurity", from_user=admin, chat=group)
    assert not any("Unanswered CAPTCHA" in (request.text or "") for request in requests)


@pytest.mark.parametrize("invalidated_by", ["revoked_rights", "disabled_flag", "wrong_reply"])
async def test_security_message_editor_rechecks_access_and_prompt(test_client: TestClient, invalidated_by: str) -> None:
    admin, group, chat, message = await _panel_group(test_client)
    await _click(test_client, admin, chat, message, "edit")
    state = test_client.dispatcher.fsm.get_context(bot=test_client.bot, chat_id=group.id, user_id=admin.id)
    data = await state.get_data()
    user = await ChatModel.get_by_tid(admin.id)
    assert user is not None
    if invalidated_by == "revoked_rights":
        await ChatAdminModel.find(ChatAdminModel.chat.id == chat.iid, ChatAdminModel.user.id == user.iid).delete()
    elif invalidated_by == "disabled_flag":
        await set_feature(test_client, "welcome_security_settings_panel", False, chat_tid=group.id)
    prompt = MessageFactory.create(
        text="Prompt",
        from_user=admin,
        chat=group,
        message_id=data["ws_settings_prompt_id"] + int(invalidated_by == "wrong_reply"),
    )
    response = MessageFactory.create(text="Do not save this", from_user=admin, chat=group, reply_to_message=prompt)
    await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=response))
    assert (await GreetingsModel.get_by_chat_iid(chat.iid)).security_note is None
