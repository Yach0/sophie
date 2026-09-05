"""End-to-end tests for the in-chat welcome-security captcha lifecycle.

The legacy deep-link path lives in test_welcomesecurity_e2e.py / test_legacy_buttons.py.
This file covers the primary flow: a member joins → is muted → gets a captcha prompt →
solves the emoji captcha → is unmuted, plus the rules-gate, ephemeral, autokick and command
variants. State is real (the Link-query fix lets the handlers resolve chat/rules/greetings
against mongomock), so nothing between the callback and the DB is mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import Message, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.constants import WELCOMESECURITY_KICK_TIMEOUT_HOURS
from sophie_bot.db.models import ChatModel, GreetingsModel, RulesModel, WSUserModel
from sophie_bot.db.models.greetings import WelcomeSecurity
from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.welcomesecurity.callbacks import (
    WelcomeSecurityConfirmCB,
    WelcomeSecurityExpireCB,
    WelcomeSecurityRulesAgreeCB,
)
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import initiate_captcha
from tests.e2e.helpers import (
    create_test_user_and_group,
    grant_admin,
    grant_bot_admin,
    join_group,
    next_user_id,
    set_feature,
)


def _restricts(requests: list, user_id: int) -> list:
    return [
        request
        for request in requests
        if request.request_type == RequestType.RESTRICT_CHAT_MEMBER and request.params.get("user_id") == user_id
    ]


async def _enable_ws(group_tid: int, *, note: str | None = None) -> ChatModel:
    chat = await ChatModel.get_by_tid(group_tid)
    assert chat is not None
    greetings = GreetingsModel(
        chat=chat.iid,
        welcome_security=WelcomeSecurity(enabled=True),
        note=Saveable(text=note) if note else None,
    )
    await greetings.save()
    return chat


async def _solve_fsm_captcha(test_client: TestClient, user_id: int) -> None:
    """Make the pending FSM captcha solvable by aligning the emoji rows."""
    state = test_client.dispatcher.fsm.get_context(bot=test_client.bot, chat_id=user_id, user_id=user_id)
    data = await state.get_data()
    captcha = dict(data["captcha"])
    captcha["front_row"] = list(captcha["back_row"])
    await state.update_data(captcha=captcha)


# ---------------------------------------------------------------------------
# Join → mute → prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_with_ws_mutes_and_prompts(test_client: TestClient) -> None:
    _adder, group, _model = await create_test_user_and_group(test_client, group_title="WS Join Group")
    await grant_bot_admin(group.id)
    await _enable_ws(group.id)

    newbie = User(id=next_user_id(), is_bot=False, first_name="Newbie")
    requests = await join_group(test_client, group, newbie)

    assert _restricts(requests, newbie.id), "A joining user should be muted by welcome-security"
    sends = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert any(request.params.get("reply_markup") for request in sends), "A captcha prompt should be sent"

    chat = await ChatModel.get_by_tid(group.id)
    user = await ChatModel.get_by_tid(newbie.id)
    assert chat is not None and user is not None
    assert await WSUserModel.is_user(user.iid, chat.iid) is not None, "The pending user is tracked for autokick"


# ---------------------------------------------------------------------------
# Solve → unmute
# ---------------------------------------------------------------------------


async def _register_pending_user(test_client: TestClient, group_tid: int) -> tuple[ChatModel, ChatModel, Message]:
    chat = await ChatModel.get_by_tid(group_tid)
    assert chat is not None
    newbie = User(id=next_user_id(), is_bot=False, first_name="Solver")
    await ChatModel.upsert_user(newbie)
    user = await ChatModel.get_by_tid(newbie.id)
    assert user is not None
    await WSUserModel.ensure_user(user, chat, is_join_request=False)
    captcha_message = await initiate_captcha(user, chat)
    return chat, user, captcha_message


@pytest.mark.asyncio
async def test_correct_captcha_unmutes_and_clears_pending(test_client: TestClient) -> None:
    _adder, group, _model = await create_test_user_and_group(test_client, group_title="WS Solve Group")
    await grant_bot_admin(group.id)
    chat = await _enable_ws(group.id)

    _chat, user, captcha_message = await _register_pending_user(test_client, group.id)
    await _solve_fsm_captcha(test_client, user.tid)

    confirm = WelcomeSecurityConfirmCB(chat_iid=str(chat.iid)).pack()
    from_user = User(id=user.tid, is_bot=False, first_name="Solver")
    requests = await test_client.send_callback(confirm, from_user=from_user, message=captcha_message)

    assert _restricts(requests, user.tid), "Passing the captcha should unmute the user"
    assert await WSUserModel.is_user(user.iid, chat.iid) is None, "The pending row should be cleared on pass"


@pytest.mark.asyncio
async def test_wrong_captcha_keeps_user_muted(test_client: TestClient) -> None:
    _adder, group, _model = await create_test_user_and_group(test_client, group_title="WS Wrong Group")
    await grant_bot_admin(group.id)
    chat = await _enable_ws(group.id)

    _chat, user, captcha_message = await _register_pending_user(test_client, group.id)
    # Do NOT align the rows: the captcha stays unsolved.

    confirm = WelcomeSecurityConfirmCB(chat_iid=str(chat.iid)).pack()
    from_user = User(id=user.tid, is_bot=False, first_name="Solver")
    requests = await test_client.send_callback(confirm, from_user=from_user, message=captcha_message)

    assert not _restricts(requests, user.tid), "A wrong answer must not unmute the user"
    assert await WSUserModel.is_user(user.iid, chat.iid) is not None, "The user stays pending after a wrong answer"


@pytest.mark.asyncio
async def test_captcha_with_rules_requires_agreement(test_client: TestClient) -> None:
    _adder, group, _model = await create_test_user_and_group(test_client, group_title="WS Rules Group")
    await grant_bot_admin(group.id)
    chat = await _enable_ws(group.id)
    await RulesModel.set_rules(chat.iid, Saveable(text="Be excellent to each other"))

    _chat, user, captcha_message = await _register_pending_user(test_client, group.id)
    await _solve_fsm_captcha(test_client, user.tid)
    from_user = User(id=user.tid, is_bot=False, first_name="Solver")

    # Solving the emoji is not enough: rules must be shown, and the user still pending.
    confirm = WelcomeSecurityConfirmCB(chat_iid=str(chat.iid)).pack()
    after_confirm = await test_client.send_callback(confirm, from_user=from_user, message=captcha_message)
    assert not _restricts(after_confirm, user.tid), "The rules gate must hold the unmute"
    assert await WSUserModel.is_user(user.iid, chat.iid) is not None

    # Agreeing to the rules completes the pass.
    agree = WelcomeSecurityRulesAgreeCB(chat_iid=str(chat.iid)).pack()
    after_agree = await test_client.send_callback(agree, from_user=from_user, message=captcha_message)
    assert _restricts(after_agree, user.tid), "Agreeing to the rules should unmute the user"
    assert await WSUserModel.is_user(user.iid, chat.iid) is None


# ---------------------------------------------------------------------------
# Ephemeral captcha
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ephemeral_captcha_prompts_each_member_privately(test_client: TestClient) -> None:
    _adder, group, _model = await create_test_user_and_group(test_client, group_title="WS Ephemeral Group")
    await grant_bot_admin(group.id)
    await _enable_ws(group.id)
    await set_feature("welcomecaptcha_ephemeral", True, chat_tid=group.id)

    first = User(id=next_user_id(), is_bot=False, first_name="AlphaJoiner")
    second = User(id=next_user_id(), is_bot=False, first_name="BetaJoiner")
    requests = await join_group(test_client, group, first, second)

    assert _restricts(requests, first.id) and _restricts(requests, second.id), "Both joiners are muted"
    prompts = [
        request
        for request in requests
        if request.request_type == RequestType.SEND_MESSAGE and request.params.get("reply_markup")
    ]
    assert len(prompts) == 2, "Each member gets their own ephemeral captcha prompt"


# ---------------------------------------------------------------------------
# Autokick timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autokick_kicks_stale_unpassed_user(test_client: TestClient) -> None:
    _adder, group, _model = await create_test_user_and_group(test_client, group_title="WS Autokick Group")
    await grant_bot_admin(group.id)
    chat = await _enable_ws(group.id)

    stale = User(id=next_user_id(), is_bot=False, first_name="Ghost")
    await ChatModel.upsert_user(stale)
    stale_model = await ChatModel.get_by_tid(stale.id)
    assert stale_model is not None
    pending = await WSUserModel.ensure_user(stale_model, chat, is_join_request=False)
    pending.added_at = datetime.now(UTC) - timedelta(hours=WELCOMESECURITY_KICK_TIMEOUT_HOURS + 1)
    await pending.save()

    start = len(test_client.capture)
    await KickUnpassedUsers().handle()
    requests = test_client.capture.all_requests[start:]

    kicks = [
        request
        for request in requests
        if request.request_type == RequestType.UNBAN_CHAT_MEMBER and request.params.get("user_id") == stale.id
    ]
    assert kicks, "A user who never solved the captcha within the window should be kicked"
    assert not [
        request
        for request in requests
        if request.request_type == RequestType.BAN_CHAT_MEMBER and request.params.get("user_id") == stale.id
    ], "The timed-out user must be kicked, never permanently banned"
    assert await WSUserModel.is_user(stale_model.iid, chat.iid) is None, "The pending row is removed after autokick"


@pytest.mark.asyncio
async def test_autokick_leaves_recent_user_alone(test_client: TestClient) -> None:
    _adder, group, _model = await create_test_user_and_group(test_client, group_title="WS Recent Group")
    await grant_bot_admin(group.id)
    chat = await _enable_ws(group.id)

    recent = User(id=next_user_id(), is_bot=False, first_name="Fresh")
    await ChatModel.upsert_user(recent)
    recent_model = await ChatModel.get_by_tid(recent.id)
    assert recent_model is not None
    await WSUserModel.ensure_user(recent_model, chat, is_join_request=False)

    start = len(test_client.capture)
    await KickUnpassedUsers().handle()
    requests = test_client.capture.all_requests[start:]

    assert not [
        request
        for request in requests
        if request.request_type in (RequestType.BAN_CHAT_MEMBER, RequestType.UNBAN_CHAT_MEMBER)
        and request.params.get("user_id") == recent.id
    ], "A user still inside the window must not be kicked"
    assert await WSUserModel.is_user(recent_model.iid, chat.iid) is not None


# ---------------------------------------------------------------------------
# Config commands persist state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_welcomecaptcha_command_persists(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="WS Cmd Group")
    await grant_admin(group.id, admin.id)

    await test_client.send_command(command="welcomecaptcha", from_user=admin, args="on", chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    assert greetings.welcome_security is not None and greetings.welcome_security.enabled is True


@pytest.mark.asyncio
async def test_welcomecaptcha_command_configures_expiry(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="WS Expiry Cmd Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="welcomecaptcha", from_user=admin, args="6h", chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    assert greetings.welcome_security is not None
    assert greetings.welcome_security.enabled is True
    assert greetings.welcome_security.expire == timedelta(hours=6)
    assert any("6 hours" in (request.text or "") for request in requests)


@pytest.mark.asyncio
async def test_welcomesecurity_expiry_button_persists_without_enabling_captcha(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="WS Expiry Button Group")
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    settings_message = MessageFactory.create(text="Welcome Security", from_user=bot_user, chat=group)
    callback = WelcomeSecurityExpireCB(seconds=int(timedelta(hours=12).total_seconds())).pack()

    await test_client.send_callback(callback, from_user=admin, message=settings_message)

    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    assert greetings.welcome_security is not None
    assert greetings.welcome_security.enabled is False
    assert greetings.welcome_security.expire == timedelta(hours=12)


@pytest.mark.asyncio
async def test_welcomecaptcha_command_requires_admin(test_client: TestClient) -> None:
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="WS Cmd NoAdmin")
    stranger = User(id=next_user_id(), is_bot=False, first_name="Stranger")
    await ChatModel.upsert_user(stranger)

    requests = await test_client.send_command(command="welcomecaptcha", from_user=stranger, args="on", chat=group)

    assert any("administrator" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_welcomerestrict_command_persists(test_client: TestClient) -> None:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="WS Restrict Cmd Group")
    await grant_admin(group.id, admin.id)

    await test_client.send_command(command="welcomerestrict", from_user=admin, args="2h", chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    assert greetings.welcome_mute is not None and greetings.welcome_mute.enabled is True
