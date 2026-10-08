from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.db.models.greetings import GreetingsModel, WelcomeSecurity
from sophie_bot.db.models.notes import Saveable
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.modules.welcomesecurity.callbacks import (
    WelcomeSecurityConfirmCB,
    WelcomeSecurityRulesAgreeCB,
)
from sophie_bot.modules.welcomesecurity.utils_.complete_captcha import complete_captcha
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import (
    initiate_captcha,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction
from tests.e2e.helpers import create_test_user_and_group, join_group, send_join_request


@pytest.mark.asyncio
async def test_legacy_welcomesecurity_start_uses_live_membership_check(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    user_wrapper = test_client.create_user(
        user_id=930001,
        first_name="Captcha",
        username="captcha_member",
    )
    group = ChatFactory.create_group(chat_id=-1009300010001, title="Captcha Group")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    user_db = await ChatModel.get_by_tid(user_wrapper.user.id)
    group_db = await ChatModel.get_by_tid(group.id)

    assert user_db is not None
    assert group_db is not None

    await GreetingsModel(chat=group_db.iid, welcome_security=WelcomeSecurity(enabled=True)).save()
    membership = await UserInGroupModel.get_user_in_group(user_db.iid, group_db.iid)
    assert membership is not None
    assert membership.id is not None
    await membership.delete()
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.legacy_button.WSUserModel.is_user",
        AsyncMock(return_value=SimpleNamespace(is_join_request=False)),
    )

    requests = await test_client.send_command(
        command="start",
        args=f"btnwelcomesecuritystart_{group.id}",
        from_user=user_wrapper.user,
    )

    assert requests, "Bot should answer the legacy welcome-security start command"
    assert requests[-1].request_type.value == "sendPhoto"
    assert requests[-1].chat_id == user_wrapper.user.id
    assert "Complete the" in (requests[-1].params.get("caption") or "")


@pytest.mark.asyncio
async def test_join_request_captcha_e2e_preserves_state_across_rules_agreement(
    test_client: TestClient,
    test_services: ApplicationServices,
    test_dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    approve_join_request = AsyncMock(return_value=True)
    monkeypatch.setattr(test_client.bot, "approve_chat_join_request", approve_join_request)

    user_wrapper = test_client.create_user(
        user_id=930002,
        first_name="JoinRequester",
        username="join_requester",
    )
    group = ChatFactory.create_group(chat_id=-1009300010002, title="Join Request Group")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    user_db = await ChatModel.get_by_tid(user_wrapper.user.id)
    group_db = await ChatModel.get_by_tid(group.id)

    assert user_db is not None
    assert group_db is not None

    greetings = GreetingsModel(
        chat=group_db.iid,
        welcome_security=WelcomeSecurity(enabled=True),
        note=Saveable(text="Welcome to the group\n{rules}"),
    )
    rules = RulesModel(chat=group_db.iid, text="Be nice to each other.")

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.captcha_confirm.RulesModel.get_rules",
        AsyncMock(return_value=rules),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.captcha_confirm.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=greetings),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.complete_captcha.ws_on_user_passed",
        AsyncMock(return_value=True),
    )

    await WSUserModel.ensure_user(user_db, group_db, is_join_request=True)

    captcha_message = await initiate_captcha(
        user_db,
        group_db,
        is_join_request=True,
        bot=test_services.bot,
        dispatcher=test_dispatcher,
    )

    state = test_dispatcher.fsm.get_context(
        bot=test_client.bot, chat_id=user_wrapper.user.id, user_id=user_wrapper.user.id
    )
    state_data = await state.get_data()
    captcha_data = dict(state_data["captcha"])
    captcha_data["front_row"] = list(captcha_data["back_row"])
    await state.update_data(captcha=captcha_data)

    confirm_data = WelcomeSecurityConfirmCB(chat_iid=str(group_db.iid), is_join_request=True).pack()
    confirm_requests = await test_client.send_callback(
        confirm_data, from_user=user_wrapper.user, message=captcha_message
    )

    assert confirm_requests, "Confirming a solved captcha should update the DM captcha message"

    agree_data = WelcomeSecurityRulesAgreeCB(chat_iid=str(group_db.iid), is_join_request=True).pack()
    completion_requests = await test_client.send_callback(
        agree_data, from_user=user_wrapper.user, message=captcha_message
    )

    approve_join_request.assert_awaited_once_with(chat_id=group.id, user_id=user_wrapper.user.id)

    # Captcha flow already showed rules in DM; no welcome/rules should be sent to the group
    group_messages = [request for request in completion_requests if request.chat_id == group.id and request.text]
    assert not any("Be nice to each other." in (request.text or "") for request in group_messages)
    assert not any("Welcome to the group" in (request.text or "") for request in group_messages)

    await test_services.redis.delete(f"chat_ws_join_request:{group_db.iid}:{user_db.iid}")


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["completing", "expiring", "exempting"])
async def test_duplicate_join_request_does_not_approve_durable_transition(
    test_client: TestClient, transition: str
) -> None:
    user, group, user_db = await create_test_user_and_group(test_client)
    group_db = await ChatModel.get_by_tid(group.id)
    assert group_db is not None
    await GreetingsModel(chat=group_db.iid, welcome_security=WelcomeSecurity(enabled=True)).save()
    pending = await WSUserModel.ensure_user(user_db, group_db, True)
    assert await pending.claim_transition(transition) is not None
    requests = await send_join_request(test_client, group, user)
    assert not any(request.request_type.value in ("approveChatJoinRequest", "sendPhoto") for request in requests)
    stored = await WSUserModel.get(pending.id)
    assert stored is not None and stored.transition == transition


@pytest.mark.asyncio
async def test_completion_survives_approval_service_event_and_bypass_consumption(
    test_client: TestClient, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, group, user_db = await create_test_user_and_group(test_client)
    group_db = await ChatModel.get_by_tid(group.id)
    assert group_db is not None
    greetings = await GreetingsModel(chat=group_db.iid, welcome_security=WelcomeSecurity(enabled=True)).save()
    pending = await WSUserModel.ensure_user(user_db, group_db, True)
    result = await execute_restriction(test_client.bot, RestrictionAction.MUTE, group.id, user.id)
    assert result.applied
    original_approve = test_client.bot.approve_chat_join_request

    async def approve_with_event(**kwargs: int) -> bool:
        approved = await original_approve(**kwargs)
        await join_group(test_client, group, user)
        assert await test_services.redis.get(f"chat_ws_join_request:{group_db.iid}:{user_db.iid}") is None
        return approved

    approval = AsyncMock(side_effect=approve_with_event)
    monkeypatch.setattr(test_client.bot, "approve_chat_join_request", approval)
    await complete_captcha(
        user_db,
        group_db,
        greetings,
        SimpleNamespace(chat=SimpleNamespace(id=user.id), message_id=42),
        bot=test_client.bot,
        redis=test_services.redis,
    )
    assert await WSUserModel.get(pending.id) is None
    requests = test_client.capture.all_requests
    approval.assert_awaited_once_with(chat_id=group.id, user_id=user.id)
    restores = [request for request in requests if request.request_type.value == "restrictChatMember"]
    assert len(restores) == 2
    assert restores[-1].params["permissions"]["can_send_messages"]
