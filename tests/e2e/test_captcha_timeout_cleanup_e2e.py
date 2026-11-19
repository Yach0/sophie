"""Offline Telegram event → persisted session → expiry sweep regressions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from aiogram import Bot
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    DeclineChatJoinRequest,
    DeleteEphemeralMessage,
    DeleteMessage,
    DeleteMessages,
    RestrictChatMember,
    SendMediaGroup,
    SendMessage,
    TelegramMethod,
    UnbanChatMember,
)
from aiogram.types import Chat, Message, User
from aiogram_test_framework import TestClient

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, GreetingsModel, WSUserModel
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.db.models.greetings import CleanWelcome, WelcomeSecurity
from sophie_bot.db.models.notes import NoteFile, Saveable
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecurityConfirmCB
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import initiate_captcha
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import (
    create_test_user_and_group,
    grant_bot_admin,
    join_group,
    leave_group,
    next_user_id,
    send_join_request,
)


@dataclass
class TelegramProbe:
    calls: list[TelegramMethod[object]] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    deletion_error: str | None = None
    fail_action: bool = False
    action_error: str = "CHAT_ADMIN_REQUIRED"
    after_action: Callable[[], Awaitable[None]] | None = None
    fail_album_followup: bool = False
    fail_mute: bool = False
    mute_returns_false: bool = False
    failed_album_followups: list[SendMessage] = field(default_factory=list)

    def deletions(self, chat_tid: int) -> set[int]:
        deleted: set[int] = set()
        for method in self.calls:
            if isinstance(method, DeleteMessage) and method.chat_id == chat_tid:
                deleted.add(method.message_id)
            elif isinstance(method, DeleteMessages) and method.chat_id == chat_tid:
                deleted.update(method.message_ids)
        return deleted

    def ephemeral_deletions(self, chat_tid: int, user_tid: int) -> set[int]:
        return {
            method.ephemeral_message_id
            for method in self.calls
            if isinstance(method, DeleteEphemeralMessage)
            and method.chat_id == chat_tid
            and method.receiver_user_id == user_tid
        }

    def actions(self) -> list[TelegramMethod[object]]:
        return [method for method in self.calls if isinstance(method, (UnbanChatMember, DeclineChatJoinRequest))]


@pytest.fixture
async def telegram_probe(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TelegramProbe:
    probe = TelegramProbe()
    original = test_client.bot.session.make_request

    async def make_request(bot: Bot, method: TelegramMethod[object], timeout: int | None = None) -> object:
        probe.calls.append(method)
        if isinstance(method, RestrictChatMember) and probe.fail_mute:
            if probe.mute_returns_false:
                return False
            raise TelegramBadRequest(method=method, message="CHAT_ADMIN_REQUIRED")
        if (
            isinstance(method, SendMessage)
            and probe.fail_album_followup
            and method.reply_markup is not None
            and method.reply_parameters is not None
            and method.reply_parameters.message_id in {message.message_id for message in probe.messages}
        ):
            probe.failed_album_followups.append(method)
            raise TelegramBadRequest(method=method, message="ALBUM_FOLLOWUP_FAILED")
        if isinstance(method, (DeleteMessage, DeleteEphemeralMessage)) and probe.deletion_error:
            if probe.deletion_error == "forbidden":
                raise TelegramForbiddenError(method=method, message="Forbidden: bot is not a member of the chat")
            raise TelegramBadRequest(method=method, message=probe.deletion_error)
        if isinstance(method, (UnbanChatMember, DeclineChatJoinRequest)) and probe.fail_action:
            raise TelegramBadRequest(method=method, message=probe.action_error)
        result = await original(bot, method, timeout=timeout)
        if isinstance(method, SendMediaGroup):
            # The upstream mock returns True for albums. Telegram returns every
            # sent Message; give the real sender that external API contract.
            result = [
                Message(
                    message_id=test_client.bot.session._get_next_message_id(),
                    date=datetime.now(UTC),
                    chat=Chat(id=method.chat_id, type="supergroup"),
                    from_user=User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie"),
                    caption=media.caption,
                )
                for media in method.media
            ]
        if isinstance(method, (UnbanChatMember, DeclineChatJoinRequest)) and probe.after_action:
            callback, probe.after_action = probe.after_action, None
            await callback()
        if isinstance(result, Message):
            parameters = method.model_dump()
            ephemeral = parameters.get("ephemeral_message_parameters") or {}
            receiver = parameters.get("receiver_user_id") or ephemeral.get("receiver_user_id")
            if receiver:
                result = result.model_copy(
                    update={
                        "message_id": 0,
                        "ephemeral_message_id": result.message_id,
                        "receiver_user": User(id=receiver, is_bot=False, first_name="Recipient"),
                    }
                )
            probe.messages.append(result)
        elif isinstance(result, list):
            probe.messages.extend(message for message in result if isinstance(message, Message))
        return result

    monkeypatch.setattr(test_client.bot.session, "make_request", make_request)
    return probe


async def _setup(test_client: TestClient) -> tuple[Chat, ChatModel]:
    _adder, group, _user = await create_test_user_and_group(test_client)
    await grant_bot_admin(group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await GreetingsModel(
        chat=chat.iid, welcome_security=WelcomeSecurity(enabled=True, expire=timedelta(minutes=1))
    ).save()
    return group, chat


async def _pending(user: User, chat: ChatModel) -> WSUserModel:
    user_db = await ChatModel.get_by_tid(user.id)
    assert user_db is not None
    pending = await WSUserModel.is_user(user_db.iid, chat.iid)
    assert pending is not None
    pending.added_at = datetime.now(UTC) - timedelta(hours=3)
    await pending.save()
    return pending


def _prompts(probe: TelegramProbe, group: Chat) -> set[int]:
    return {
        message.message_id
        for message in probe.messages
        if message.chat.id == group.id and message.ephemeral_message_id is None
    }


async def _complete_captcha(test_client: TestClient, user: User, chat: ChatModel) -> None:
    user_db = await ChatModel.get_by_tid(user.id)
    assert user_db is not None
    captcha_message = await initiate_captcha(user_db, chat, bot=test_client.bot, dispatcher=test_client.dispatcher)
    state = test_client.dispatcher.fsm.get_context(bot=test_client.bot, chat_id=user.id, user_id=user.id)
    data = await state.get_data()
    captcha = dict(data["captcha"])
    captcha["front_row"] = list(captcha["back_row"])
    await state.update_data(captcha=captcha)
    await test_client.send_callback(
        WelcomeSecurityConfirmCB(chat_iid=str(chat.iid)).pack(),
        from_user=user,
        message=captcha_message,
    )
    assert await WSUserModel.is_user(user_db.iid, chat.iid) is None


async def test_expiry_deletes_only_owned_join_and_prompt_without_clean_service(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Pending")
    await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    assert pending.membership_join_message_id is not None
    expected = {pending.membership_join_message_id, *_prompts(telegram_probe, group)}
    assert len(expected) == 1
    # A later ordinary message must never replace the known join cleanup target.
    await test_client.send_message("unrelated", from_user=newbie, chat=group)
    # Legacy, unscoped Redis values are not proof of ownership by this join session.
    await test_services.redis.set(f"chat_ws_message:{chat.iid}:{pending.user.ref.id}", 999999)
    await test_services.redis.set(f"join_request_message:{chat.iid}:{pending.user.ref.id}", 999998)

    telegram_probe.calls.clear()
    scheduler = KickUnpassedUsers(test_services)
    await scheduler.handle()
    await scheduler.handle()

    assert len(telegram_probe.actions()) == 1
    assert telegram_probe.deletions(group.id) == expected
    expected_ephemeral = {
        message.ephemeral_message_id
        for message in telegram_probe.messages
        if message.chat.id == group.id and message.ephemeral_message_id is not None
    }
    assert telegram_probe.ephemeral_deletions(group.id, newbie.id) == expected_ephemeral
    assert await WSUserModel.get(pending.id) is None


@pytest.mark.parametrize(
    "reason",
    [
        "message to delete not found",
        "message can't be deleted",
        "CHAT_ADMIN_REQUIRED",
        "not enough rights to delete messages",
        "forbidden",
    ],
)
async def test_expected_cleanup_errors_finish_expiry_without_repeating_kick(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
    reason: str,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Pending")
    await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    expected = {pending.membership_join_message_id, *_prompts(telegram_probe, group)}
    telegram_probe.deletion_error = reason

    await KickUnpassedUsers(test_services).handle()
    await KickUnpassedUsers(test_services).handle()

    assert telegram_probe.deletions(group.id) == expected
    assert len(telegram_probe.ephemeral_deletions(group.id, newbie.id)) == 1
    assert len(telegram_probe.actions()) == 1
    assert await WSUserModel.get(pending.id) is None


async def test_unexpected_cleanup_error_propagates_but_does_not_repeat_kick(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Pending")
    await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    telegram_probe.deletion_error = "UNEXPECTED"
    with pytest.raises(TelegramBadRequest, match="UNEXPECTED"):
        await KickUnpassedUsers(test_services).handle()
    await KickUnpassedUsers(test_services).handle()
    assert len(telegram_probe.actions()) == 1
    assert await WSUserModel.get(pending.id) is None


@pytest.mark.parametrize("join_request", [False, True])
async def test_failed_expiry_action_keeps_session_and_messages_for_retry(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
    join_request: bool,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Pending")
    if join_request:
        await send_join_request(test_client, group, newbie)
    else:
        await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    telegram_probe.fail_action = True
    await KickUnpassedUsers(test_services).handle()
    assert await WSUserModel.get(pending.id) is not None
    assert not telegram_probe.deletions(group.id)
    telegram_probe.fail_action = False
    await KickUnpassedUsers(test_services).handle()
    assert await WSUserModel.get(pending.id) is None
    assert telegram_probe.deletions(group.id)


async def test_decline_cleans_group_request_prompt_without_kick_or_join_deletion(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Requester")
    await send_join_request(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    assert pending.is_join_request
    assert pending.membership_join_message_id is None
    expected = _prompts(telegram_probe, group)
    assert expected
    await KickUnpassedUsers(test_services).handle()
    assert len(telegram_probe.actions()) == 1
    assert isinstance(telegram_probe.actions()[0], DeclineChatJoinRequest)
    assert telegram_probe.deletions(group.id) == expected
    assert not telegram_probe.deletions(newbie.id)
    assert await WSUserModel.get(pending.id) is None


@pytest.mark.parametrize("transition", ["rejoin", "passed", "completed"])
async def test_stale_sweep_does_not_touch_newer_verified_or_completed_session(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
    transition: Literal["rejoin", "passed", "completed"],
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Pending")
    await join_group(test_client, group, newbie)
    snapshot = await _pending(newbie, chat)
    if transition == "rejoin":
        await join_group(test_client, group, newbie)
        # Even an expired newer record is outside this stale sweep's session.
        await _pending(newbie, chat)
    elif transition == "passed":
        current = await WSUserModel.get(snapshot.id)
        assert current is not None
        current.passed = True
        await current.save()
    else:
        await snapshot.delete()
    telegram_probe.calls.clear()
    await KickUnpassedUsers(test_services).process_user(snapshot)
    assert not telegram_probe.actions()
    assert not telegram_probe.deletions(group.id)


async def test_shared_join_service_survives_one_members_expiry(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    pending_user = User(id=next_user_id(), is_bot=False, first_name="Pending")
    successful_user = User(id=next_user_id(), is_bot=False, first_name="Other")
    await join_group(test_client, group, pending_user, successful_user)
    pending = await _pending(pending_user, chat)
    await _complete_captcha(test_client, successful_user, chat)
    await KickUnpassedUsers(test_services).handle()
    assert len(telegram_probe.actions()) == 1
    assert telegram_probe.actions()[0].user_id == pending_user.id
    assert pending.membership_join_message_id not in telegram_probe.deletions(group.id)
    assert not telegram_probe.deletions(group.id)
    assert len(telegram_probe.ephemeral_deletions(group.id, pending_user.id)) == 1
    assert not telegram_probe.ephemeral_deletions(group.id, successful_user.id)
    assert await WSUserModel.get(pending.id) is None


async def test_bot_join_and_removal_are_clean_without_blanket_clean_service(
    test_client: TestClient,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    robot = User(id=next_user_id(), is_bot=True, first_name="Robot")
    await join_group(test_client, group, robot)
    robot_db = await ChatModel.get_by_tid(robot.id)
    assert robot_db is not None
    membership = await UserInGroupModel.get_user_in_group(robot_db.iid, chat.iid)
    assert membership is not None
    assert telegram_probe.deletions(group.id) == {membership.joined_message_id}
    assert await WSUserModel.is_user(robot_db.iid, chat.iid) is None
    await leave_group(test_client, group, robot)
    assert len(telegram_probe.deletions(group.id)) == 2


async def test_rejoin_expiry_cleans_only_the_new_session_messages(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Rejoining")
    await join_group(test_client, group, newbie)
    old = await _pending(newbie, chat)
    telegram_probe.messages.clear()
    await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    assert pending.membership_join_message_id != old.membership_join_message_id
    expected_ephemeral = {
        message.ephemeral_message_id for message in telegram_probe.messages if message.ephemeral_message_id is not None
    }
    await KickUnpassedUsers(test_services).handle()
    assert telegram_probe.deletions(group.id) == {pending.membership_join_message_id}
    assert telegram_probe.ephemeral_deletions(group.id, newbie.id) == expected_ephemeral


async def test_shared_human_and_bot_join_service_is_preserved(
    test_client: TestClient,
    telegram_probe: TelegramProbe,
) -> None:
    group, _chat = await _setup(test_client)
    robot = User(id=next_user_id(), is_bot=True, first_name="Robot")
    human = User(id=next_user_id(), is_bot=False, first_name="Human")
    await join_group(test_client, group, robot, human)
    assert not telegram_probe.deletions(group.id)


async def test_membership_boundary_change_before_captcha_setup_fences_expiry(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Rejoining")
    await join_group(test_client, group, newbie)
    snapshot = await _pending(newbie, chat)
    membership = await UserInGroupModel.get_user_in_group(snapshot.user.ref.id, chat.iid)
    assert membership is not None and membership.joined_message_id is not None
    membership.joined_message_id += 1
    await membership.save()
    await KickUnpassedUsers(test_services).handle()
    assert not telegram_probe.actions()
    assert not telegram_probe.deletions(group.id)
    assert not telegram_probe.ephemeral_deletions(group.id, newbie.id)


async def test_legacy_pending_membership_message_is_not_assumed_to_be_a_join(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Legacy")
    await test_client.send_message("ordinary user message", from_user=newbie, chat=group)
    user_db = await ChatModel.get_by_tid(newbie.id)
    assert user_db is not None
    await WSUserModel.ensure_user(user_db, chat, is_join_request=False)
    pending = await _pending(newbie, chat)
    assert pending.membership_join_message_id is None
    telegram_probe.calls.clear()
    await KickUnpassedUsers(test_services).handle()
    assert len(telegram_probe.actions()) == 1
    assert not telegram_probe.deletions(group.id)


async def test_leave_event_during_kick_does_not_lose_known_timeout_cleanup(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Pending")
    await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    expected_ephemeral = {
        message.ephemeral_message_id for message in telegram_probe.messages if message.ephemeral_message_id is not None
    }

    async def deliver_leave() -> None:
        await leave_group(test_client, group, newbie)

    telegram_probe.after_action = deliver_leave
    await KickUnpassedUsers(test_services).handle()
    assert telegram_probe.deletions(group.id) == {pending.membership_join_message_id}
    assert telegram_probe.ephemeral_deletions(group.id, newbie.id) == expected_ephemeral
    assert await WSUserModel.get(pending.id) is None


async def test_shared_bot_join_service_is_also_preserved(
    test_client: TestClient,
    telegram_probe: TelegramProbe,
) -> None:
    group, _chat = await _setup(test_client)
    first = User(id=next_user_id(), is_bot=True, first_name="First bot")
    second = User(id=next_user_id(), is_bot=True, first_name="Second bot")
    await join_group(test_client, group, first, second)
    assert not telegram_probe.deletions(group.id)


async def test_public_album_security_prompt_cleans_all_known_messages(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    greetings.security_note = Saveable(
        text="Verify",
        files=[
            NoteFile(id="first-photo", type=ContentType.PHOTO),
            NoteFile(id="second-photo", type=ContentType.PHOTO),
        ],
    )
    await greetings.save()
    newbie = User(id=next_user_id(), is_bot=False, first_name="Pending")
    await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    expected = {pending.membership_join_message_id, *_prompts(telegram_probe, group)}
    assert len(expected) == 4  # Join, two album photos, and the CAPTCHA button follow-up.
    await KickUnpassedUsers(test_services).handle()
    assert telegram_probe.deletions(group.id) == expected
    assert not telegram_probe.ephemeral_deletions(group.id, newbie.id)


@pytest.mark.parametrize("join_request", [False, True])
async def test_partial_public_album_is_persisted_and_cleaned_after_followup_failure(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
    join_request: bool,
) -> None:
    group, chat = await _setup(test_client)
    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    album = Saveable(
        text="Verify",
        buttons=[[Button(text="Verify", action=ButtonAction.captcha)]],
        files=[
            NoteFile(id="first-photo", type=ContentType.PHOTO),
            NoteFile(id="second-photo", type=ContentType.PHOTO),
        ],
    )
    greetings.security_note = album
    greetings.join_request_message = album
    await greetings.save()
    telegram_probe.fail_album_followup = True
    newbie = User(id=next_user_id(), is_bot=False, first_name="Partial delivery")
    if join_request:
        await send_join_request(test_client, group, newbie)
    else:
        await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    assert len(telegram_probe.failed_album_followups) == 1
    album_ids = {
        message.message_id
        for message in telegram_probe.messages
        if message.chat.id == group.id and message.text is None
    }
    assert len(album_ids) == 2
    expected = album_ids | ({pending.membership_join_message_id} if not join_request else set())
    assert set(pending.cleanup_message_ids) == expected
    assert not pending.cleanup_ephemeral_message_ids

    telegram_probe.calls.clear()
    await KickUnpassedUsers(test_services).handle()
    await KickUnpassedUsers(test_services).handle()
    assert telegram_probe.deletions(group.id) == expected
    assert not telegram_probe.deletions(newbie.id)
    assert len(telegram_probe.actions()) == 1
    assert isinstance(telegram_probe.actions()[0], DeclineChatJoinRequest if join_request else UnbanChatMember)
    assert await WSUserModel.get(pending.id) is None


async def test_request_prompt_survives_previous_welcome_deletion_failure(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    greetings.clean_welcome = CleanWelcome(enabled=True, last_msg=424242)
    greetings.join_request_message = Saveable(text="Request verification prompt")
    await greetings.save()
    telegram_probe.deletion_error = "UNEXPECTED_DELETE_FAILURE"
    newbie = User(id=next_user_id(), is_bot=False, first_name="Requester")
    await send_join_request(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    prompt_ids = {
        message.message_id
        for message in telegram_probe.messages
        if message.chat.id == group.id and message.text == "Request verification prompt"
    }
    assert len(prompt_ids) == 1
    assert set(pending.cleanup_message_ids) == prompt_ids

    telegram_probe.deletion_error = None
    telegram_probe.calls.clear()
    await KickUnpassedUsers(test_services).handle()
    assert telegram_probe.deletions(group.id) == prompt_ids
    assert len(telegram_probe.actions()) == 1
    assert isinstance(telegram_probe.actions()[0], DeclineChatJoinRequest)
    assert await WSUserModel.get(pending.id) is None


@pytest.mark.parametrize("shared_join", [False, True])
@pytest.mark.parametrize("mute_returns_false", [False, True])
async def test_initial_mute_failure_keeps_only_owned_join_for_timeout_cleanup(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
    shared_join: bool,
    mute_returns_false: bool,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Unmuted")
    robot = User(id=next_user_id(), is_bot=True, first_name="Other bot")
    telegram_probe.fail_mute = True
    telegram_probe.mute_returns_false = mute_returns_false
    await join_group(test_client, group, newbie, *([robot] if shared_join else []))
    pending = await _pending(newbie, chat)
    assert pending.membership_join_message_id is not None
    assert not _prompts(telegram_probe, group)
    assert not pending.cleanup_ephemeral_message_ids
    expected = set() if shared_join else {pending.membership_join_message_id}
    assert set(pending.cleanup_message_ids) == expected

    telegram_probe.calls.clear()
    await KickUnpassedUsers(test_services).handle()
    await KickUnpassedUsers(test_services).handle()
    assert telegram_probe.deletions(group.id) == expected
    assert len(telegram_probe.actions()) == 1
    assert isinstance(telegram_probe.actions()[0], UnbanChatMember)
    assert await WSUserModel.get(pending.id) is None


async def test_successful_captcha_preserves_join_and_does_not_run_timeout_cleanup(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Successful")
    await join_group(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    await _complete_captcha(test_client, newbie, chat)
    assert await WSUserModel.get(pending.id) is None
    await KickUnpassedUsers(test_services).handle()
    assert not telegram_probe.actions()
    assert not telegram_probe.deletions(group.id)
    assert not telegram_probe.ephemeral_deletions(group.id, newbie.id)


async def test_sophie_join_cleanup_preserves_self_greeting_and_previous_welcome(
    test_client: TestClient,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    greetings = await GreetingsModel.get_by_chat_iid(chat.iid)
    greetings.clean_welcome = CleanWelcome(enabled=True, last_msg=424242)
    await greetings.save()
    robot = User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie")
    await join_group(test_client, group, robot)
    assert any("Thank you for choosing Sophie" in (message.text or "") for message in telegram_probe.messages)
    assert len(telegram_probe.deletions(group.id)) == 1
    assert 424242 not in telegram_probe.deletions(group.id)


async def test_missing_request_is_a_decline_failure_and_keeps_retry_state(
    test_client: TestClient,
    test_services: ApplicationServices,
    telegram_probe: TelegramProbe,
) -> None:
    group, chat = await _setup(test_client)
    newbie = User(id=next_user_id(), is_bot=False, first_name="Requester")
    await send_join_request(test_client, group, newbie)
    pending = await _pending(newbie, chat)
    telegram_probe.fail_action = True
    telegram_probe.action_error = "HIDE_REQUESTER_MISSING"
    await KickUnpassedUsers(test_services).handle()
    assert await WSUserModel.get(pending.id) is not None
    assert not telegram_probe.deletions(group.id)
    assert len(telegram_probe.actions()) == 1
    assert isinstance(telegram_probe.actions()[0], DeclineChatJoinRequest)
