from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import InlineKeyboardMarkup
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory, UserFactory
from bson.dbref import DBRef

from sophie_bot.config import CONFIG
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.modules.connections.utils.connection import check_connection_permissions


def _flatten_keyboard(markup: InlineKeyboardMarkup) -> list[tuple[str, str | None, str | None]]:
    return [
        (button.text, button.url, button.callback_data)
        for button_row in markup.inline_keyboard
        for button in button_row
    ]


def _link_matches_iid(value: object, expected_iid: object) -> bool:
    if isinstance(value, DBRef):
        return str(value.id) == str(expected_iid)
    return str(value) == str(expected_iid)


async def _create_group_and_user(
    test_client: TestClient,
    *,
    group_tid: int,
    user_tid: int,
    group_title: str,
) -> tuple[ChatModel, ChatModel, Any, Any]:
    user_wrapper = test_client.create_user(
        user_id=user_tid,
        first_name=f"User{user_tid}",
        username=f"user_{user_tid}",
    )
    group = ChatFactory.create_group(chat_id=group_tid, title=group_title)

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    user_db = await ChatModel.get_by_tid(user_wrapper.user.id)
    group_db = await ChatModel.get_by_tid(group.id)
    assert user_db is not None
    assert group_db is not None
    await UserInGroupModel.ensure_user_in_group(user_db, group_db)

    return user_db, group_db, user_wrapper, group


@pytest.mark.asyncio
async def test_legacy_note_button_renders_migrated_button_rows(
    test_client: TestClient,
) -> None:

    _user_db, group_db, user_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010001,
        user_tid=940001,
        group_title="Legacy Buttons Group",
    )

    note = NoteModel(
        chat_id=group.id,
        chat=group_db,
        names=("menu",),
        text="Legacy menu",
        buttons=[
            [Button(text="URL", action=ButtonAction.url, data="https://example.com/path")],
            [Button(text="Sophie DM", action=ButtonAction.sophiedm)],
            [Button(text="Nested Note", action=ButtonAction.note, data="child")],
            [Button(text="Hash Note", action=ButtonAction.note, data="child")],
            [Button(text="Rules", action=ButtonAction.rules)],
            [Button(text="Connect", action=ButtonAction.connect)],
            [Button(text="Delete", action=ButtonAction.delmsg)],
            [Button(text="Captcha", action=ButtonAction.captcha)],
        ],
        version=2,
    )

    get_note = AsyncMock(return_value=note)
    with (
        patch.object(UserInGroupModel, "get_user_in_group", AsyncMock(return_value=SimpleNamespace())),
        patch.object(NoteModel, "get_by_notenames", get_note),
    ):
        requests = await test_client.send_command(
            command="start",
            args=f"btnnotesm_menu_{group.id}",
            from_user=user_wrapper.user,
        )

    get_note.assert_awaited_once_with(group_db.iid, ("menu",))

    assert requests, "Legacy note deep link should send the note"
    response = requests[-1]
    assert response.request_type.value == "sendMessage"
    assert response.reply_markup is not None

    markup = InlineKeyboardMarkup.model_validate(response.reply_markup)
    buttons = _flatten_keyboard(markup)

    assert ("URL", "https://example.com/path", None) in buttons
    assert ("Sophie DM", f"https://t.me/{CONFIG.username}", None) in buttons
    assert any(
        button_text == "Nested Note"
        and f"start=btnnotesm_child_{group.id}" in (button_url or "")
        and button_callback is None
        for button_text, button_url, button_callback in buttons
    )
    assert any(
        button_text == "Hash Note"
        and f"start=btnnotesm_child_{group.id}" in (button_url or "")
        and button_callback is None
        for button_text, button_url, button_callback in buttons
    )
    assert any(
        button_text == "Rules" and f"start=btn_rules_{group.id}" in (button_url or "") and button_callback is None
        for button_text, button_url, button_callback in buttons
    )
    assert any(
        button_text == "Connect"
        and f"start=btn_connect_start_{group.id}" in (button_url or "")
        and button_callback is None
        for button_text, button_url, button_callback in buttons
    )
    assert any(
        button_text == "Delete" and button_url is None and callback_data == f"btn_deletemsg_cb_{group.id}"
        for button_text, button_url, callback_data in buttons
    )
    assert any(
        button_text == "Captcha"
        and f"start=btnwelcomesecuritystart_{group.id}" in (button_url or "")
        and button_callback is None
        for button_text, button_url, button_callback in buttons
    )


@pytest.mark.asyncio
async def test_unmigrated_v1_note_does_not_parse_legacy_buttons_at_send_time(
    test_client: TestClient,
) -> None:

    _user_db, group_db, user_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010007,
        user_tid=940007,
        group_title="Unmigrated Legacy Buttons Group",
    )

    note = NoteModel(
        chat_id=group.id,
        chat=group_db,
        names=("menu",),
        text="Legacy menu\n[URL](btnurl:https://example.com/path)",
        version=1,
    )

    with (
        patch.object(UserInGroupModel, "get_user_in_group", AsyncMock(return_value=SimpleNamespace())),
        patch.object(NoteModel, "get_by_notenames", AsyncMock(return_value=note)),
    ):
        requests = await test_client.send_command(
            command="start",
            args=f"btnnotesm_menu_{group.id}",
            from_user=user_wrapper.user,
        )

    assert requests, "Legacy note deep link should still send the note"
    response = requests[-1]
    assert response.request_type.value == "sendMessage"
    assert "[URL](btnurl:https://example.com/path)" in (response.text or "")
    assert response.reply_markup in (None, {"inline_keyboard": []})


@pytest.mark.asyncio
async def test_legacy_note_button_rejects_users_outside_chat(
    test_client: TestClient,
) -> None:

    _member_db, group_db, _member_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010008,
        user_tid=940008,
        group_title="Private Legacy Notes Group",
    )
    outsider_wrapper = test_client.create_user(
        user_id=940009,
        first_name="Outsider",
        username="outside_notes",
    )
    note = NoteModel(
        chat_id=group.id,
        chat=group_db,
        names=("private",),
        text="Members only",
        version=2,
    )
    await note.insert()

    requests = await test_client.send_command(
        command="start",
        args=f"btnnotesm_private_{group.id}",
        from_user=outsider_wrapper.user,
    )

    outsider_db = await ChatModel.get_by_tid(outsider_wrapper.user.id)
    assert outsider_db is not None
    assert await UserInGroupModel.get_user_in_group(outsider_db.iid, group_db.iid) is None

    assert requests, "Legacy note deep link should explain why access is denied"
    assert "member of this chat" in (requests[-1].text or "")


@pytest.mark.asyncio
async def test_legacy_rules_button_resolves_chat_link(
    test_client: TestClient,
) -> None:

    _user_db, group_db, user_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010002,
        user_tid=940002,
        group_title="Legacy Rules Group",
    )
    rules = RulesModel(chat=group_db, text="Be kind and stay on topic.", version=2)
    get_rules = AsyncMock(return_value=rules)

    with patch.object(RulesModel, "get_rules", get_rules):
        requests = await test_client.send_command(
            command="start",
            args=f"btn_rules_{group.id}",
            from_user=user_wrapper.user,
        )

    get_rules.assert_awaited_once_with(group_db.iid)

    assert requests, "Legacy rules deep link should send rules"
    response_text = requests[-1].text or ""
    assert "Rules" in response_text
    assert "Be kind and stay on topic." in response_text


@pytest.mark.asyncio
async def test_legacy_connect_button_deep_link_connects_user(test_client: TestClient) -> None:
    user_db, group_db, user_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010003,
        user_tid=940003,
        group_title="Legacy Connect Group",
    )

    with patch(
        "sophie_bot.modules.connections.handlers.start_connect.check_connection_permissions",
        AsyncMock(return_value=True),
    ):
        requests = await test_client.send_command(
            command="start",
            args=f"btn_connect_start_{group.id}",
            from_user=user_wrapper.user,
        )

    assert requests, "Legacy connect deep link should reply after connecting"
    assert "Connected" in (requests[-1].text or "")

    collection = ChatConnectionModel.get_pymongo_collection()
    raw_connections = await collection.find().to_list(length=None)
    matching_connections = [
        raw_connection
        for raw_connection in raw_connections
        if _link_matches_iid(raw_connection.get("user"), user_db.iid)
    ]
    assert len(matching_connections) == 1
    assert _link_matches_iid(matching_connections[0].get("chat"), group_db.iid)

    assert await check_connection_permissions(group_db.iid, user_db.iid)


@pytest.mark.asyncio
async def test_legacy_delete_message_button_deletes_message(test_client: TestClient) -> None:
    _user_db, _group_db, user_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010004,
        user_tid=940004,
        group_title="Legacy Delete Group",
    )

    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    source_message = MessageFactory.create(
        text="Message with delete button",
        from_user=bot_user,
        chat=group,
        reply_markup=InlineKeyboardMarkup.model_validate(
            {"inline_keyboard": [[{"text": "Delete", "callback_data": f"btn_deletemsg_cb_{group.id}"}]]}
        ),
    )

    requests = await test_client.send_callback(
        f"btn_deletemsg_cb_{group.id}",
        from_user=user_wrapper.user,
        message=source_message,
    )

    assert any(request.request_type.value == "deleteMessage" for request in requests)


@pytest.mark.asyncio
async def test_legacy_welcomesecurity_callback_redirects_to_deep_link(test_client: TestClient) -> None:
    _user_db, _group_db, user_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010005,
        user_tid=940005,
        group_title="Legacy WS Redirect Group",
    )

    bot_user = UserFactory.create(user_id=42, first_name="Sophie", username="sophie_bot", is_bot=True)
    source_message = MessageFactory.create(
        text="Welcome security",
        from_user=bot_user,
        chat=group,
        reply_markup=InlineKeyboardMarkup.model_validate(
            {"inline_keyboard": [[{"text": "Verify", "callback_data": "ws_verify"}]]}
        ),
    )

    requests = await test_client.send_callback(
        "ws_verify",
        from_user=user_wrapper.user,
        message=source_message,
    )

    assert requests, "Legacy welcome-security callback should answer with a redirect URL"
    redirect_request = requests[-1]
    assert redirect_request.request_type.value == "answerCallbackQuery"
    assert (
        redirect_request.params.get("url") == f"https://t.me/{CONFIG.username}?start=btnwelcomesecuritystart_{group.id}"
    )


@pytest.mark.asyncio
async def test_legacy_welcomesecurity_deep_link_still_starts_captcha(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    user_db, group_db, user_wrapper, group = await _create_group_and_user(
        test_client,
        group_tid=-1009400010006,
        user_tid=940006,
        group_title="Legacy Captcha Group",
    )

    await RulesModel(chat=group_db, text="Captcha rules", version=2).insert()
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.WSUserModel.is_user",
        AsyncMock(return_value=SimpleNamespace(is_join_request=True)),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.FederationManageService.get_federation_for_chat",
        AsyncMock(return_value=None),
    )

    requests = await test_client.send_command(
        command="start",
        args=f"btnwelcomesecuritystart_{group.id}",
        from_user=user_wrapper.user,
    )

    assert user_db.iid
    assert requests, "Legacy welcome-security deep link should start captcha"
    assert requests[-1].request_type.value == "sendPhoto"
    assert requests[-1].chat_id == user_wrapper.user.id
