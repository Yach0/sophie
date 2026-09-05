"""End-to-end tests for what happens when a user actually joins a group.

These drive `NewUserMiddleware` / `LeaveUserMiddleware` through the real dispatcher via
`join_group` / `leave_group`, and assert on the Telegram calls the bot made plus the
GreetingsModel state — the product behaviour the command-level greetings tests never reach.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import User
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.constants import WELCOMESECURITY_JOIN_TIMEOUT_MINUTES
from sophie_bot.db.models import ChatModel, GreetingsModel, RulesModel
from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.db.models.global_user_whitelist import GlobalUserWhitelistModel
from sophie_bot.db.models.notes import Saveable
from sophie_bot.services.redis import aredis
from tests.e2e.helpers import (
    create_test_user_and_group,
    grant_admin,
    grant_bot_admin,
    join_group,
    leave_group,
    next_user_id,
    set_feature,
)


async def _greetings(chat_tid: int) -> GreetingsModel:
    chat = await ChatModel.get_by_tid(chat_tid)
    assert chat is not None
    return await GreetingsModel.get_by_chat_iid(chat.iid)


def _sends(requests: list) -> list:
    return [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]


def _deleted_ids(requests: list) -> list[int]:
    """Message ids the bot asked Telegram to delete (deleteMessage or deleteMessages)."""
    ids: list[int] = []
    for request in requests:
        ids.extend(request.params.get("message_ids", []))
        if "message_id" in request.params and request.request_type != RequestType.EDIT_MESSAGE_TEXT:
            ids.append(request.params["message_id"])
    return ids


async def _setup_group(test_client: TestClient) -> tuple[User, object]:
    adder, group, _model = await create_test_user_and_group(test_client, group_title="Greetings Join Group")
    await grant_bot_admin(group.id)
    return adder, group


@pytest.mark.asyncio
async def test_join_sends_default_welcome(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Newbie")

    requests = await join_group(test_client, group, newbie)

    sends = _sends(requests)
    assert sends, "A welcome message should be sent on join"
    assert str(newbie.id) in (sends[-1].text or ""), "The welcome should mention the new member"


@pytest.mark.asyncio
async def test_custom_welcome_text_is_sent(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await GreetingsModel.change_welcome_message(chat.iid, Saveable(text="Bienvenue les amis"))

    newbie = User(id=next_user_id(), is_bot=False, first_name="Newbie")
    requests = await join_group(test_client, group, newbie)

    assert any("Bienvenue les amis" in (request.text or "") for request in _sends(requests))


@pytest.mark.asyncio
async def test_welcome_disabled_sends_nothing(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await GreetingsModel.change_state_welcome(chat.iid, False)

    newbie = User(id=next_user_id(), is_bot=False, first_name="Newbie")
    requests = await join_group(test_client, group, newbie)

    assert not _sends(requests), "No welcome should be sent when welcome is disabled"


@pytest.mark.asyncio
async def test_clean_service_deletes_the_join_message(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    greetings = await _greetings(group.id)
    await greetings.set_service_clean_status(True)

    newbie = User(id=next_user_id(), is_bot=False, first_name="Newbie")
    requests = await join_group(test_client, group, newbie)

    assert _deleted_ids(requests), "The join service message should be deleted when clean_service is on"


@pytest.mark.asyncio
async def test_clean_welcome_tracks_and_replaces_the_previous_welcome(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    greetings = await _greetings(group.id)
    await greetings.set_clean_welcome_status(True)

    first = await join_group(test_client, group, User(id=next_user_id(), is_bot=False, first_name="First"))
    first_send = _sends(first)[-1]
    stored = await _greetings(group.id)
    assert stored.clean_welcome is not None
    assert stored.clean_welcome.last_msg == first_send.response.message_id, (
        "clean_welcome should record the id of the welcome it just sent"
    )

    second = await join_group(test_client, group, User(id=next_user_id(), is_bot=False, first_name="Second"))
    assert first_send.response.message_id in _deleted_ids(second), (
        "The previous welcome should be deleted on the next join"
    )


@pytest.mark.asyncio
async def test_welcome_carries_the_rules_button(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await RulesModel.set_rules(chat.iid, Saveable(text="Be nice to each other"))

    requests = await join_group(test_client, group, User(id=next_user_id(), is_bot=False, first_name="Newbie"))

    send = _sends(requests)[-1]
    assert send.params.get("reply_markup"), "A welcome for a chat with rules should carry the rules button"


@pytest.mark.asyncio
async def test_welcome_mute_restricts_new_member(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    greetings = await _greetings(group.id)
    await greetings.set_status_welcomemute(True, timedelta(hours=1))

    newbie = User(id=next_user_id(), is_bot=False, first_name="Newbie")
    requests = await join_group(test_client, group, newbie)

    restricts = [
        request
        for request in requests
        if request.request_type == RequestType.RESTRICT_CHAT_MEMBER and request.params.get("user_id") == newbie.id
    ]
    assert restricts, "welcome_mute should restrict the new member"


@pytest.mark.asyncio
async def test_welcome_mute_skips_globally_whitelisted_new_member(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    greetings = await _greetings(group.id)
    await greetings.set_status_welcomemute(True, timedelta(hours=1))
    newbie = User(id=next_user_id(), is_bot=False, first_name="Allowed Newbie")
    await GlobalUserWhitelistModel.add_user(newbie.id)

    requests = await join_group(test_client, group, newbie)

    assert not [
        request
        for request in requests
        if request.request_type == RequestType.RESTRICT_CHAT_MEMBER and request.params.get("user_id") == newbie.id
    ]


@pytest.mark.asyncio
async def test_ephemeral_greeting_is_per_member_and_untracked(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    greetings = await _greetings(group.id)
    await greetings.set_clean_welcome_status(True)
    await set_feature("greetings_ephemeral", True, chat_tid=group.id)

    first = User(id=next_user_id(), is_bot=False, first_name="AlphaJoiner")
    second = User(id=next_user_id(), is_bot=False, first_name="BetaJoiner")
    requests = await join_group(test_client, group, first, second)

    assert len(_sends(requests)) == 2, "Each joining member gets their own ephemeral greeting"
    stored = await _greetings(group.id)
    # Ephemeral greetings live only for their recipient, so none is handed to clean-welcome.
    assert stored.clean_welcome is not None
    assert stored.clean_welcome.last_msg is None


@pytest.mark.asyncio
async def test_admin_adder_still_welcomes_but_skips_captcha(test_client: TestClient) -> None:
    adder, group = await _setup_group(test_client)
    await grant_admin(group.id, adder.id)
    greetings = await _greetings(group.id)
    await greetings.set_status_welcomesecurity(True)

    newbie = User(id=next_user_id(), is_bot=False, first_name="Newbie")
    requests = await join_group(test_client, group, newbie, added_by=adder)

    assert _sends(requests), "An admin-added user still gets a welcome"
    assert not [
        request
        for request in requests
        if request.request_type == RequestType.RESTRICT_CHAT_MEMBER and request.params.get("user_id") == newbie.id
    ], "Captcha mute must be skipped when an admin added the user"


@pytest.mark.asyncio
async def test_bot_added_triggers_self_welcome(test_client: TestClient) -> None:
    adder, group = await _setup_group(test_client)
    bot_user = User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie")

    requests = await join_group(test_client, group, bot_user, added_by=adder)

    joined = " ".join(request.text or "" for request in _sends(requests))
    assert "Sophie" in joined, "Adding the bot should trigger the self-welcome"


@pytest.mark.asyncio
async def test_join_request_joiner_only_cleans_service(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    greetings = await _greetings(group.id)
    await greetings.set_service_clean_status(True)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    newbie = User(id=next_user_id(), is_bot=False, first_name="Requester")
    # Register the user and mark them as having joined via an (already-greeted) join request.
    await ChatModel.upsert_user(newbie)
    user = await ChatModel.get_by_tid(newbie.id)
    assert user is not None
    await aredis.set(f"chat_ws_join_request:{chat.iid}:{user.iid}", "1")

    requests = await join_group(test_client, group, newbie)

    assert not _sends(requests), "A join-request user already got their greeting; no second welcome"


@pytest.mark.asyncio
async def test_stale_join_skips_captcha_mute(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    greetings = await _greetings(group.id)
    await greetings.set_status_welcomesecurity(True)

    old_date = datetime.now(UTC) - timedelta(minutes=WELCOMESECURITY_JOIN_TIMEOUT_MINUTES + 5)
    newbie = User(id=next_user_id(), is_bot=False, first_name="LateNewbie")
    requests = await join_group(test_client, group, newbie, date=old_date)

    assert not [request for request in requests if request.request_type == RequestType.RESTRICT_CHAT_MEMBER], (
        "A stale join must not trigger the captcha mute"
    )


@pytest.mark.asyncio
async def test_leave_removes_user_in_group_row(test_client: TestClient) -> None:
    _adder, group = await _setup_group(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Leaver")
    await join_group(test_client, group, newbie)

    chat = await ChatModel.get_by_tid(group.id)
    user = await ChatModel.get_by_tid(newbie.id)
    assert chat is not None and user is not None
    assert await UserInGroupModel.get_user_in_group(user.iid, chat.iid) is not None

    await leave_group(test_client, group, newbie)

    assert await UserInGroupModel.get_user_in_group(user.iid, chat.iid) is None
