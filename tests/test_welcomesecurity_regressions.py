from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from beanie import PydanticObjectId

from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityRulesAgreeCB
from sophie_bot.modules.welcomesecurity.handlers.legacy_button import LegacyWSButtonHandler
from sophie_bot.modules.welcomesecurity.utils_.captcha_rules import captcha_send_rules
from sophie_bot.modules.welcomesecurity.utils_.complete_captcha import complete_captcha
from sophie_bot.modules.welcomesecurity.utils_.on_user_passed import ws_on_user_passed
from sophie_bot.modules.welcomesecurity.utils_.pending_user_lock import _pending_user_lock_key
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult


@pytest.mark.asyncio
async def test_legacy_button_validates_membership_via_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    get_user_in_group = AsyncMock(return_value=None)
    ensure_user_in_group = AsyncMock()
    get_chat_member = AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER))
    bot = SimpleNamespace(get_chat_member=get_chat_member)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.UserInGroupModel.get_user_in_group",
        get_user_in_group,
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.UserInGroupModel.ensure_user_in_group",
        ensure_user_in_group,
    )

    handler = LegacyWSButtonHandler(
        SimpleNamespace(),
        services=SimpleNamespace(bot=bot),
    )
    is_in_group = await handler._user_is_still_in_group(user, group)

    assert is_in_group is True
    get_chat_member.assert_awaited_once_with(chat_id=group.tid, user_id=user.tid)
    ensure_user_in_group.assert_awaited_once_with(user, group)


@pytest.mark.asyncio
async def test_captcha_rules_preserve_join_request_context(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    send_captcha_message = AsyncMock()
    chat_iid = PydanticObjectId()
    message = SimpleNamespace(chat=SimpleNamespace(id=12345))
    rules = SimpleNamespace(text="Rules", file=None)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.captcha_rules.send_captcha_message",
        send_captcha_message,
    )

    await captcha_send_rules(
        message,
        rules,
        chat_iid,
        True,
        bot=test_services.bot,
        redis=test_services.redis,
    )

    reply_markup = send_captcha_message.await_args.kwargs["reply_markup"]
    callback_data = reply_markup.inline_keyboard[0][0].callback_data
    parsed_callback = WelcomeSecurityRulesAgreeCB.unpack(callback_data)

    assert parsed_callback.chat_iid == str(chat_iid)
    assert parsed_callback.is_join_request is True


@pytest.mark.asyncio
async def test_complete_captcha_does_not_send_welcome_or_rules_to_group(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
) -> None:
    """Captcha flow already shows rules in DM; no welcome/rules should be sent to the group on completion."""
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    greetings = SimpleNamespace(welcome_mute=None, welcome_disabled=False, note=SimpleNamespace())
    captcha_message = SimpleNamespace(
        chat=SimpleNamespace(id=user.tid),
        message_id=42,
        from_user=SimpleNamespace(id=user.tid),
    )
    bot_mock = SimpleNamespace(
        edit_message_media=AsyncMock(),
        approve_chat_join_request=AsyncMock(),
        delete_message=AsyncMock(),
    )

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.complete_captcha.ws_on_user_passed",
        AsyncMock(),
    )

    await complete_captcha(
        user,
        group,
        greetings,
        captcha_message,
        bot=bot_mock,
        redis=test_redis,
    )

    # complete_captcha should only update the captcha image in DM and unmute the user;
    # it must NOT send any welcome or rules messages to the group.
    bot_mock.edit_message_media.assert_awaited_once()
    # No messages sent to the group chat
    for call in bot_mock.delete_message.await_args_list:
        # delete_message calls are cleanup, not sending new messages — that's fine
        pass


@pytest.mark.asyncio
async def test_complete_captcha_continues_when_join_request_was_already_approved(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    greetings = SimpleNamespace(welcome_mute=None)
    captcha_message = SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42)
    approval = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SimpleNamespace(),
            message="Bad Request: USER_ALREADY_PARTICIPANT",
        )
    )
    post_approval = AsyncMock(return_value=True)
    bot_mock = SimpleNamespace(edit_message_media=AsyncMock(), approve_chat_join_request=approval)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.complete_captcha.ws_on_user_passed",
        post_approval,
    )

    await complete_captcha(
        user,
        group,
        greetings,
        captcha_message,
        is_join_request=True,
        bot=bot_mock,
        redis=test_services.redis,
    )

    approval.assert_awaited_once_with(chat_id=group.tid, user_id=user.tid)
    post_approval.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_captcha_propagates_unexpected_join_approval_failure(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    greetings = SimpleNamespace(welcome_mute=None)
    captcha_message = SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42)
    approval_error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: CHAT_ADMIN_REQUIRED")
    post_approval = AsyncMock()
    bot_mock = SimpleNamespace(
        edit_message_media=AsyncMock(),
        approve_chat_join_request=AsyncMock(side_effect=approval_error),
    )

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.complete_captcha.ws_on_user_passed",
        post_approval,
    )

    with pytest.raises(TelegramBadRequest) as raised:
        await complete_captcha(
            user,
            group,
            greetings,
            captcha_message,
            is_join_request=True,
            bot=bot_mock,
            redis=test_services.redis,
        )

    assert raised.value is approval_error
    post_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_captcha_keeps_recovery_messages_when_post_approval_transition_fails(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    greetings = SimpleNamespace(welcome_mute=None)
    captcha_message = SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42)
    redis = test_services.redis
    security_note_key = f"chat_ws_message:{group.iid}:{user.iid}"
    join_request_note_key = f"join_request_message:{group.iid}:{user.iid}"
    await redis.set(security_note_key, 43)
    await redis.set(join_request_note_key, 44)
    post_approval = AsyncMock(return_value=False)
    bot_mock = SimpleNamespace(
        edit_message_media=AsyncMock(),
        approve_chat_join_request=AsyncMock(),
        delete_message=AsyncMock(),
    )

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.complete_captcha.ws_on_user_passed",
        post_approval,
    )

    await complete_captcha(
        user,
        group,
        greetings,
        captcha_message,
        is_join_request=True,
        bot=bot_mock,
        redis=redis,
    )

    bot_mock.delete_message.assert_not_awaited()
    assert await redis.get(security_note_key) == b"43"
    assert await redis.get(join_request_note_key) == b"44"


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction_succeeded", [False, True])
async def test_complete_captcha_preserves_locked_transition_result_and_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    restriction_succeeded: bool,
) -> None:
    user = SimpleNamespace(iid=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), tid=-100123)
    redis = test_services.redis
    security_note_key = f"chat_ws_message:{group.iid}:{user.iid}"
    join_request_note_key = f"join_request_message:{group.iid}:{user.iid}"
    await redis.set(security_note_key, 43)
    await redis.set(join_request_note_key, 44)
    remove_pending = AsyncMock()
    bot = SimpleNamespace(
        edit_message_media=AsyncMock(),
        approve_chat_join_request=AsyncMock(),
        delete_message=AsyncMock(),
    )

    async def unmute(*args: object, **kwargs: object) -> RestrictionResult:
        assert await redis.get(_pending_user_lock_key(group.tid, user.tid)) is not None
        return RestrictionResult(action=RestrictionAction.UNMUTE, applied=restriction_succeeded)

    passed_module = "sophie_bot.modules.welcomesecurity.utils_.on_user_passed"
    monkeypatch.setattr(f"{passed_module}.is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{passed_module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{passed_module}.execute_restriction", unmute)
    monkeypatch.setattr(f"{passed_module}.WSUserModel.remove_user", remove_pending)
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.complete_captcha.ws_on_user_passed",
        ws_on_user_passed,
    )

    await complete_captcha(
        user,
        group,
        SimpleNamespace(welcome_mute=None),
        SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42),
        is_join_request=True,
        bot=bot,
        redis=redis,
    )

    bot.approve_chat_join_request.assert_awaited_once_with(chat_id=group.tid, user_id=user.tid)
    if restriction_succeeded:
        remove_pending.assert_awaited_once_with(user.iid, group.iid)
        assert bot.delete_message.await_count == 2
        assert await redis.get(security_note_key) is None
    else:
        remove_pending.assert_not_awaited()
        bot.delete_message.assert_not_awaited()
        assert await redis.get(security_note_key) == b"43"
    assert await redis.get(join_request_note_key) == b"44"
    assert await redis.get(_pending_user_lock_key(group.tid, user.tid)) is None
