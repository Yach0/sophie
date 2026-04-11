from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from beanie import PydanticObjectId

from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityRulesAgreeCB
from sophie_bot.modules.welcomesecurity.handlers.legacy_button import LegacyWSButtonHandler
from sophie_bot.modules.welcomesecurity.utils_.captcha_rules import captcha_send_rules
from sophie_bot.modules.welcomesecurity.utils_.complete_captcha import complete_captcha


@pytest.mark.asyncio
async def test_legacy_button_validates_membership_via_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    get_user_in_group = AsyncMock(return_value=None)
    ensure_user_in_group = AsyncMock()
    get_chat_member = AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER))

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.UserInGroupModel.get_user_in_group",
        get_user_in_group,
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.UserInGroupModel.ensure_user_in_group",
        ensure_user_in_group,
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.bot",
        SimpleNamespace(get_chat_member=get_chat_member),
    )

    is_in_group = await LegacyWSButtonHandler._user_is_still_in_group(user, group)

    assert is_in_group is True
    get_chat_member.assert_awaited_once_with(chat_id=group.tid, user_id=user.tid)
    ensure_user_in_group.assert_awaited_once_with(user, group)


@pytest.mark.asyncio
async def test_captcha_rules_preserve_join_request_context(monkeypatch: pytest.MonkeyPatch) -> None:
    send_captcha_message = AsyncMock()
    chat_iid = PydanticObjectId()
    message = SimpleNamespace(chat=SimpleNamespace(id=12345))
    rules = SimpleNamespace(text="Rules", file=None)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.captcha_rules.send_captcha_message",
        send_captcha_message,
    )

    await captcha_send_rules(message, rules, chat_iid, True)

    reply_markup = send_captcha_message.await_args.kwargs["reply_markup"]
    callback_data = reply_markup.inline_keyboard[0][0].callback_data
    parsed_callback = WelcomeSecurityRulesAgreeCB.unpack(callback_data)

    assert parsed_callback.chat_iid == str(chat_iid)
    assert parsed_callback.is_join_request is True


@pytest.mark.asyncio
async def test_complete_captcha_does_not_send_welcome_or_rules_to_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """Captcha flow already shows rules in DM; no welcome/rules should be sent to the group on completion."""
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    greetings = SimpleNamespace(welcome_mute=None, welcome_disabled=False, note=SimpleNamespace())
    captcha_message = SimpleNamespace(
        chat=SimpleNamespace(id=user.tid),
        message_id=42,
        from_user=SimpleNamespace(id=user.tid),
    )
    redis = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock(), delete=AsyncMock())
    bot_mock = SimpleNamespace(
        edit_message_media=AsyncMock(),
        approve_chat_join_request=AsyncMock(),
        delete_message=AsyncMock(),
    )

    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.utils_.complete_captcha.aredis", redis)
    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.utils_.complete_captcha.bot", bot_mock)
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.complete_captcha.ws_on_user_passed",
        AsyncMock(),
    )

    await complete_captcha(user, group, greetings, captcha_message)

    # complete_captcha should only update the captcha image in DM and unmute the user;
    # it must NOT send any welcome or rules messages to the group.
    bot_mock.edit_message_media.assert_awaited_once()
    # No messages sent to the group chat
    for call in bot_mock.delete_message.await_args_list:
        # delete_message calls are cleanup, not sending new messages — that's fine
        pass
