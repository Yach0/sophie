from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import httpx2
import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.methods import ApproveChatJoinRequest, DeclineChatJoinRequest, TelegramMethod
from aiogram.types import Chat, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.types import CapturedRequest, RequestType
from pyfederation import AsyncFederationClient

from sophie_bot.config import Config, OfdServerConfig
from sophie_bot.db.models.antispam import (
    AntispamConfirmedSpamModel,
    AntispamExclusionModel,
    AntispamIncidentModel,
    AntispamIncidentResolution,
    AntispamMode,
    AntispamPolicy,
    AntispamSettingsModel,
)
from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.db.models.greetings import GreetingsModel, WelcomeSecurity
from sophie_bot.db.models.group_user_whitelist import GroupUserWhitelistModel
from sophie_bot.db.models.log import LogModel
from sophie_bot.db.models.spam_match import SpamMatchModel
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.modules.antispam import ussr
from sophie_bot.modules.antispam.callbacks import AntispamIncidentCallback, IncidentOperation
from sophie_bot.modules.antispam.cas import CasProvider
from sophie_bot.modules.antispam.ofd import OfdProvider
from sophie_bot.modules.antispam.registry import ProviderRegistry
from sophie_bot.modules.antispam.service import cleanup_expired_antispam_messages
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import (
    create_test_user_and_group,
    grant_admin,
    grant_bot_admin,
    join_group,
    next_user_id,
    send_join_request,
    send_reply_command,
    set_feature,
)


@dataclass
class Upstreams:
    flagged: set[int] = field(default_factory=set)
    cas_users: list[int] = field(default_factory=list)
    ofd_users: list[str] = field(default_factory=list)
    scans: list[str] = field(default_factory=list)
    ussr_texts: list[str] = field(default_factory=list)
    ussr_spam_texts: set[str] = field(default_factory=set)
    cas_status: int = 200

    def cas(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/check"
        user_tid = int(request.url.params["user_id"])
        self.cas_users.append(user_tid)
        if self.cas_status != 200:
            return httpx2.Response(self.cas_status, json={"ok": False})
        if user_tid in self.flagged:
            return httpx2.Response(200, json={"ok": True, "result": {"reason": "Spam <report>", "offenses": 2}})
        return httpx2.Response(404, json={"ok": False, "error_code": 404})

    def ofd(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/scan":
            data = json.loads(request.content)
            assert data["author"].endswith("@telegram.org")
            content = data["evidence"]["text_content"]
            self.scans.append(content)
            return httpx.Response(
                200,
                json={
                    "resolved_entities": [],
                    "scan_results": {},
                    "risk_score": 0.9 if content == "flagged content" else 0.0,
                    "suggested_action": "BLOCK_CONTENT" if content == "flagged content" else None,
                },
            )
        self.ofd_users.append(request.url.path)
        return httpx.Response(404, json={"message": "Unknown entity"})

    def ussr_predict(self, text: str, _links: tuple[str, ...] = ()) -> dict[str, float]:
        self.ussr_texts.append(text)
        return {
            "spam_probability": 0.92 if text in self.ussr_spam_texts else 0.05,
            "nsfw_probability": 0.0,
        }


@pytest_asyncio.fixture
async def upstreams(test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Upstreams]:
    upstream = Upstreams()
    config = Config(
        antispam_cas_api_key="test-key",
        antispam_cas_export_url=None,
        antispam_ofd_servers=[OfdServerConfig(id="example", name="Example OFD", url="https://ofd.example")],
    )
    registry = ProviderRegistry(config, test_services.redis)
    cas = registry.providers["cas"]
    ofd = registry.providers["ofd:example"]
    assert isinstance(cas, CasProvider) and isinstance(ofd, OfdProvider)
    await cas.close()
    await ofd.close()
    cas.client = httpx2.AsyncClient(transport=httpx2.MockTransport(upstream.cas))
    ofd.client = AsyncFederationClient(
        "https://ofd.example",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream.ofd)),
    )
    monkeypatch.setattr(test_services, "antispam", registry)
    monkeypatch.setattr(ussr, "predict_message", upstream.ussr_predict)
    try:
        yield upstream
    finally:
        await registry.close()


async def _group(test_client: TestClient, *, enabled: bool = True) -> tuple[User, Chat, ChatModel]:
    actor, group, _actor_doc = await create_test_user_and_group(test_client)
    await grant_bot_admin(group.id)
    if enabled:
        await set_feature(test_client, "antispam", True, chat_tid=group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    return actor, group, chat


async def _policy(chat: ChatModel, **values: object) -> None:
    policy = AntispamPolicy.model_validate(values)
    await AntispamSettingsModel.update_policy(chat, lambda _previous: policy)


def _actions(requests: list[CapturedRequest], kind: RequestType) -> list[CapturedRequest]:
    return [request for request in requests if request.request_type == kind]


def _text(requests: list[CapturedRequest]) -> str:
    return " ".join(request.text or "" for request in _actions(requests, RequestType.SEND_MESSAGE))


def _buttons(request: CapturedRequest) -> list[tuple[str, str]]:
    markup = request.params.get("reply_markup")
    if not markup:
        return []
    rows = markup.get("inline_keyboard", []) if isinstance(markup, dict) else markup.inline_keyboard
    result: list[tuple[str, str]] = []
    for row in rows:
        for button in row:
            if isinstance(button, dict):
                result.append((button["text"], button["callback_data"]))
            else:
                result.append((button.text, button.callback_data or ""))
    return result


async def test_antispam_status_and_mode_commands(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client)
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command("antispam", admin, chat=group)
    status = _text(requests)
    assert "<b>Mode</b>: Normal (Mute)" in status
    assert "<b>USSR</b>: Active" in status
    assert "<b>Combot Anti-Spam</b>: Active" in status
    assert "<b>Example OFD</b>: Active" in status

    requests = await test_client.send_command("antispam", admin, args="off", chat=group)
    assert "<b>Mode</b>: Off" in _text(requests)
    assert "<b>USSR</b>: Off" in _text(requests)
    assert "<b>Combot Anti-Spam</b>: Off" in _text(requests)

    requests = await test_client.send_command("antispam", admin, args="strict", chat=group)
    assert "<b>Mode</b>: Strict (Ban)" in _text(requests)

    requests = await test_client.send_command("antispam_3party", admin, args="off", chat=group)
    third_party_off = _text(requests)
    assert "<b>USSR</b>: Active" in third_party_off
    assert "<b>Combot Anti-Spam</b>: Off" in third_party_off
    assert "<b>Example OFD</b>: Off" in third_party_off

    policy = await AntispamSettingsModel.get_policy(chat)
    assert policy.mode == AntispamMode.STRICT and not policy.third_party_enabled
    assert not upstreams.cas_users and not upstreams.scans


async def test_normal_mode_mutes_ussr_match_and_undo_excludes_with_training_text(
    test_client: TestClient,
    test_services: ApplicationServices,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client)
    await grant_admin(group.id, admin.id)
    member = User(id=next_user_id(), is_bot=False, first_name="Member")
    upstreams.ussr_spam_texts.add("free crypto scam")
    await _policy(chat, mode="normal", third_party_enabled=False)

    requests = await test_client.send_message("free crypto scam", member, group)
    assert _actions(requests, RequestType.RESTRICT_CHAT_MEMBER)
    assert not _actions(requests, RequestType.BAN_CHAT_MEMBER)
    assert not _actions(requests, RequestType.DELETE_MESSAGE)

    notices = _actions(requests, RequestType.SEND_MESSAGE)
    assert len(notices) == 1
    notice = notices[0]
    assert "muted" in (notice.text or "")
    assert "automatically deleted in about 10 minutes" in (notice.text or "")
    buttons = _buttons(notice)
    assert [label for label, _data in buttons] == ["Unmute", "Ban"]

    incident = await AntispamIncidentModel.find_one(AntispamIncidentModel.chat.id == chat.iid)
    assert incident is not None
    assert incident.source_id == "ussr"
    assert incident.original_message_text == "free crypto scam"
    assert incident.user_message_id is not None and incident.notice_message_id is not None
    assert incident.delete_at is not None
    assert await SpamMatchModel.find(SpamMatchModel.chat_tid == group.id).count() == 1

    # Admin clicks Unmute -> user unrestricted, added to antispam_exclusions with USSR text
    notice_msg = MessageFactory.create(
        message_id=incident.notice_message_id,
        text=notice.text or "",
        from_user=test_client.bot._bot_user,
        chat=group,
    )
    undo_data = AntispamIncidentCallback(op=IncidentOperation.UNDO, incident_id=str(incident.id)).pack()
    undo_requests = await test_client.send_callback(undo_data, admin, message=notice_msg)
    assert _actions(undo_requests, RequestType.RESTRICT_CHAT_MEMBER)
    assert _actions(undo_requests, RequestType.EDIT_MESSAGE_TEXT)

    exclusion = await AntispamExclusionModel.find_one(
        AntispamExclusionModel.chat.id == chat.iid,
        AntispamExclusionModel.user_tid == member.id,
    )
    assert exclusion is not None
    assert exclusion.source_id == "ussr"
    assert exclusion.original_message_text == "free crypto scam"
    assert await LogModel.find({"event": LogEvent.ANTISPAM_UNDONE.value}).count() == 1

    # Excluded user is no longer flagged on subsequent messages
    upstreams.ussr_texts.clear()
    follow_up = await test_client.send_message("free crypto scam", member, group)
    assert not upstreams.ussr_texts
    assert not _actions(follow_up, RequestType.RESTRICT_CHAT_MEMBER)

    # Scheduler deletes Sophie's notice after 10 minutes, but preserves the undone user's message
    await AntispamIncidentModel.find_one(AntispamIncidentModel.id == incident.id).update(
        {"$set": {"delete_at": datetime.now(UTC) - timedelta(minutes=1)}}
    )
    test_client.capture.clear()
    deleted = await cleanup_expired_antispam_messages(test_services)
    assert deleted == 1
    deleted_ids = [item.params["message_id"] for item in test_client.capture.get_by_type(RequestType.DELETE_MESSAGE)]
    assert deleted_ids == [incident.notice_message_id]


async def test_normal_mode_ban_button_escalates_and_scheduler_deletes_unhandled_pair(
    test_client: TestClient,
    test_services: ApplicationServices,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client)
    await grant_admin(group.id, admin.id)
    first_spammer = User(id=next_user_id(), is_bot=False, first_name="First")
    second_spammer = User(id=next_user_id(), is_bot=False, first_name="Second")
    upstreams.flagged.update((first_spammer.id, second_spammer.id))
    await _policy(chat, mode="normal")

    # First spammer: admin escalates to Ban via button
    requests = await test_client.send_message("spam 1", first_spammer, group)
    incident_one = await AntispamIncidentModel.find_one(AntispamIncidentModel.user_tid == first_spammer.id)
    assert incident_one is not None and incident_one.notice_message_id is not None
    assert incident_one.original_message_text == "spam 1"
    notice_msg = MessageFactory.create(
        message_id=incident_one.notice_message_id,
        text=_text(requests),
        from_user=test_client.bot._bot_user,
        chat=group,
    )
    ban_data = AntispamIncidentCallback(op=IncidentOperation.BAN, incident_id=str(incident_one.id)).pack()
    ban_requests = await test_client.send_callback(ban_data, admin, message=notice_msg)
    assert _actions(ban_requests, RequestType.BAN_CHAT_MEMBER)
    assert [item.params["message_id"] for item in _actions(ban_requests, RequestType.DELETE_MESSAGE)] == [
        incident_one.user_message_id
    ]
    refreshed_one = await AntispamIncidentModel.get(incident_one.id)
    assert refreshed_one is not None and refreshed_one.resolution == AntispamIncidentResolution.BANNED
    confirmed = await AntispamConfirmedSpamModel.find_one(
        AntispamConfirmedSpamModel.chat.id == chat.iid,
        AntispamConfirmedSpamModel.user_tid == first_spammer.id,
    )
    assert confirmed is not None
    assert confirmed.message_text == "spam 1"
    assert confirmed.source_id == "cas"
    assert confirmed.confirmed_by_tid == admin.id
    assert confirmed.message_timestamp is not None and confirmed.confirmed_at is not None

    # Second spammer: nobody taps any button -> scheduler deletes BOTH notice and user message after 10 minutes
    await test_client.send_message("spam 2", second_spammer, group)
    incident_two = await AntispamIncidentModel.find_one(AntispamIncidentModel.user_tid == second_spammer.id)
    assert incident_two is not None and incident_two.notice_message_id is not None
    assert incident_two.user_message_id is not None

    await AntispamIncidentModel.find_one(AntispamIncidentModel.id == incident_two.id).update(
        {"$set": {"delete_at": datetime.now(UTC) - timedelta(minutes=1)}}
    )
    test_client.capture.clear()
    deleted = await cleanup_expired_antispam_messages(test_services)
    assert deleted == 2
    deleted_ids = {item.params["message_id"] for item in test_client.capture.get_by_type(RequestType.DELETE_MESSAGE)}
    assert deleted_ids == {incident_two.notice_message_id, incident_two.user_message_id}


async def test_strict_mode_bans_shows_unban_only_and_undo_unbans_without_text(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client)
    await grant_admin(group.id, admin.id)
    user = User(id=next_user_id(), is_bot=False, first_name="Flagged")
    upstreams.flagged.add(user.id)
    await _policy(chat, mode="strict")

    requests = await test_client.send_message("hello", user, group)
    assert _actions(requests, RequestType.BAN_CHAT_MEMBER)
    assert not _actions(requests, RequestType.DELETE_MESSAGE)
    notice = _actions(requests, RequestType.SEND_MESSAGE)[0]
    assert "banned" in (notice.text or "") and "Spam &lt;report&gt;" in (notice.text or "")
    assert [label for label, _data in _buttons(notice)] == ["Unban"]

    incident = await AntispamIncidentModel.find_one(AntispamIncidentModel.user_tid == user.id)
    assert incident is not None and incident.notice_message_id is not None
    notice_msg = MessageFactory.create(
        message_id=incident.notice_message_id,
        text=notice.text or "",
        from_user=test_client.bot._bot_user,
        chat=group,
    )
    undo_data = AntispamIncidentCallback(op=IncidentOperation.UNDO, incident_id=str(incident.id)).pack()
    undo_requests = await test_client.send_callback(undo_data, admin, message=notice_msg)
    assert _actions(undo_requests, RequestType.UNBAN_CHAT_MEMBER)

    exclusion = await AntispamExclusionModel.find_one(
        AntispamExclusionModel.chat.id == chat.iid,
        AntispamExclusionModel.user_tid == user.id,
    )
    assert exclusion is not None and exclusion.source_id == "cas" and exclusion.original_message_text is None


async def test_info_shows_antispam_verdicts_for_user_id_and_reply_with_escaping(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    admin, group, _chat = await _group(test_client)
    await grant_admin(group.id, admin.id)
    user = User(id=next_user_id(), is_bot=False, first_name="Target")
    upstreams.flagged.add(user.id)
    requests = await test_client.send_command("info", admin, args=str(user.id), chat=group)
    result = _text(requests)
    assert "Anti-spam" in result and "Flagged" in result and "Clean" in result
    assert "Spam &lt;report&gt;" in result and f"https://cas.chat/query?u={user.id}" in result

    clean = User(id=next_user_id(), is_bot=False, first_name="Alice")
    replied = MessageFactory.create(text="Hello", from_user=clean, chat=group)
    requests = await send_reply_command(test_client, command="info", from_user=admin, group=group, replied=replied)
    reply_result = _text(requests)
    assert "Anti-spam" in reply_result and reply_result.count("Clean") == 2
    assert upstreams.cas_users == [user.id, clean.id]


async def test_strict_join_request_intercepted_before_captcha(
    test_client: TestClient,
    upstreams: Upstreams,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, group, chat = await _group(test_client)
    await GreetingsModel(chat=chat.iid, welcome_security=WelcomeSecurity(enabled=True)).save()
    await _policy(chat, mode="strict", notify_chat=False)
    upstreams.flagged.add(user.id)
    original = test_client.bot.session.make_request
    requests_seen: list[TelegramMethod] = []

    async def track(bot: Bot, method: TelegramMethod, timeout: int | None = None) -> object:
        requests_seen.append(method)
        if isinstance(method, (DeclineChatJoinRequest, ApproveChatJoinRequest)):
            return True
        return await original(bot, method, timeout=timeout)

    monkeypatch.setattr(test_client.bot.session, "make_request", track)
    requests = await send_join_request(test_client, group, user)
    assert [item.user_id for item in requests_seen if isinstance(item, DeclineChatJoinRequest)] == [user.id]
    assert not any(isinstance(item, ApproveChatJoinRequest) for item in requests_seen)
    assert _actions(requests, RequestType.BAN_CHAT_MEMBER)
    assert not _actions(requests, RequestType.RESTRICT_CHAT_MEMBER)
    assert await WSUserModel.count() == 0
    assert await LogModel.find({"event": LogEvent.ANTISPAM_MATCH.value, "details.join_request": True}).count() == 1


@pytest.mark.parametrize("mixed", [False, True])
async def test_strict_member_joins_remove_flagged_members_before_welcome(
    test_client: TestClient,
    upstreams: Upstreams,
    mixed: bool,
) -> None:
    _actor, group, chat = await _group(test_client)
    await _policy(chat, mode="strict", notify_chat=False)
    flagged = User(id=next_user_id(), is_bot=False, first_name="Flagged")
    clean = User(id=next_user_id(), is_bot=False, first_name="Allowed")
    upstreams.flagged.add(flagged.id)
    members = (flagged, clean) if mixed else (flagged,)
    requests = await join_group(test_client, group, *members)
    assert [item.params["user_id"] for item in _actions(requests, RequestType.BAN_CHAT_MEMBER)] == [flagged.id]
    notices = _actions(requests, RequestType.SEND_MESSAGE)
    if mixed:
        assert str(clean.id) in (notices[0].text or "")
        assert not any(str(flagged.id) in (notice.text or "") for notice in notices)
    else:
        assert not notices
        assert _actions(requests, RequestType.DELETE_MESSAGE)


async def test_third_party_scan_is_capped_while_ussr_continues_scanning(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    user, group, chat = await _group(test_client)
    await _policy(chat, mode="normal", scan_new_user_messages_limit=2, notify_chat=False)

    first = await test_client.send_message("clean content", user, group)
    second = await test_client.send_message("flagged content", user, group)
    third = await test_client.send_message("beyond quota", user, group)
    assert not _actions(first, RequestType.RESTRICT_CHAT_MEMBER)
    assert _actions(second, RequestType.RESTRICT_CHAT_MEMBER)
    assert not _actions(third, RequestType.RESTRICT_CHAT_MEMBER)
    assert upstreams.scans == ["clean content", "flagged content"]
    assert upstreams.ussr_texts == ["clean content", "flagged content", "beyond quota"]
    assert upstreams.ofd_users == [f"/entities/{user.id}@telegram.org/query"]
    user_model = await ChatModel.get_by_tid(user.id)
    assert user_model is not None
    membership = await UserInGroupModel.get_user_in_group(user_model.iid, chat.iid)
    assert membership is not None and membership.antispam_seen_messages == 2


async def test_exemptions_and_non_admin_controls(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client)
    await grant_admin(group.id, admin.id)
    allowed = User(id=next_user_id(), is_bot=False, first_name="Whitelisted")
    bot = User(id=next_user_id(), is_bot=True, first_name="Invited Bot")
    upstreams.flagged.update((admin.id, allowed.id, bot.id))
    await set_feature(test_client, "group_user_whitelist", True)
    await GroupUserWhitelistModel.add_user(group.id, allowed.id)
    await _policy(chat, mode="strict")

    await test_client.send_message("admin", admin, group)
    await test_client.send_message("whitelisted", allowed, group)
    await join_group(test_client, group, bot, added_by=admin)
    requests = await test_client.send_command("antispam", allowed, args="off", chat=group)
    assert "<b>Mode</b>: Off" not in _text(requests)
    policy = await AntispamSettingsModel.get_policy(chat)
    assert policy.mode == AntispamMode.STRICT
    assert not upstreams.cas_users and not upstreams.ofd_users and not upstreams.ussr_texts and not upstreams.scans
    assert not test_client.capture.get_by_type(RequestType.BAN_CHAT_MEMBER)
    assert (
        await LogModel.find(
            {"event": LogEvent.GROUP_WHITELIST_EXEMPTION.value, "details.subsystem": "antispam"}
        ).count()
        >= 1
    )


async def test_feature_flags_disable_commands_provider_traffic_and_scanning(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client, enabled=False)
    await grant_admin(group.id, admin.id)
    user = User(id=next_user_id(), is_bot=False, first_name="Flagged")
    upstreams.flagged.add(user.id)
    await _policy(chat, mode="strict")
    requests = await test_client.send_command("antispam", admin, chat=group)
    assert not _actions(requests, RequestType.SEND_MESSAGE)
    info_requests = await test_client.send_command("info", admin, args=str(user.id), chat=group)
    assert "Anti-spam" not in _text(info_requests)
    await test_client.send_message("flags disabled", user, group)
    assert not upstreams.cas_users and not upstreams.ofd_users and not upstreams.ussr_texts

    await set_feature(test_client, "antispam", True, chat_tid=group.id)
    await set_feature(test_client, "antispam_cas", False, chat_tid=group.id)
    await set_feature(test_client, "antispam_ofd", False, chat_tid=group.id)
    await test_client.send_message("providers disabled", user, group)
    assert not upstreams.cas_users and not upstreams.ofd_users and upstreams.ussr_texts == ["providers disabled"]

    await set_feature(test_client, "antispam_ofd", True, chat_tid=group.id)
    await set_feature(test_client, "antispam_ofd_content_scan", False, chat_tid=group.id)
    await test_client.send_message("scans disabled", user, group)
    assert upstreams.ofd_users and not upstreams.scans and not upstreams.cas_users
    assert not test_client.capture.get_by_type(RequestType.BAN_CHAT_MEMBER)


async def test_upstream_error_still_allows_normal_join_delivery(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    user, group, chat = await _group(test_client)
    await _policy(chat, mode="strict")
    upstreams.cas_status = 503
    requests = await join_group(test_client, group, user)
    assert not _actions(requests, RequestType.BAN_CHAT_MEMBER)
    assert _actions(requests, RequestType.SEND_MESSAGE)
    assert not await LogModel.find({"event": LogEvent.ANTISPAM_MATCH.value}).count()


async def test_connected_dm_can_manage_group_antispam(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client)
    await grant_admin(group.id, admin.id)
    await test_client.send_command("connect", admin, args=str(group.id))
    private_chat = Chat(id=admin.id, type="private", first_name=admin.first_name)

    requests = await test_client.send_command("antispam", admin, args="strict", chat=private_chat)
    assert "<b>Mode</b>: Strict (Ban)" in _text(requests)
    await test_client.send_command("antispam_3party", admin, args="off", chat=private_chat)
    policy = await AntispamSettingsModel.get_policy(chat)
    assert policy.mode == AntispamMode.STRICT and not policy.third_party_enabled

    target = User(id=next_user_id(), is_bot=False, first_name="Check")
    upstreams.flagged.add(target.id)
    requests = await test_client.send_command("info", admin, args=str(target.id), chat=private_chat)
    result = _text(requests)
    assert "Anti-spam" in result and "Flagged" in result


async def test_antispam_trust_exempts_long_standing_members(
    test_client: TestClient,
    upstreams: Upstreams,
) -> None:
    admin, group, chat = await _group(test_client)
    await grant_admin(group.id, admin.id)
    veteran = User(id=next_user_id(), is_bot=False, first_name="Veteran")
    upstreams.ussr_spam_texts.add("suspicious promo")

    # Default trust is 30 days
    default_status = _text(await test_client.send_command("antispam", admin, chat=group))
    assert "<b>Trust members after</b>: 1 month" in default_status or "30 days" in default_status

    # Register veteran in group and backdate first_saw to 4 days ago
    await test_client.send_message("hello", veteran, group)
    veteran_model = await ChatModel.get_by_tid(veteran.id)
    assert veteran_model is not None
    await UserInGroupModel.find_one(
        UserInGroupModel.user.id == veteran_model.iid,
        UserInGroupModel.group.id == chat.iid,
    ).update({"$set": {"first_saw": datetime.now(UTC) - timedelta(days=4)}})

    # Lower trust window to 3d -> veteran (4 days old) is skipped without any scan/lookup
    trust_reply = _text(await test_client.send_command("antispam_trust", admin, args="3d", chat=group))
    assert "3 days" in trust_reply
    upstreams.ussr_texts.clear()
    upstreams.cas_users.clear()
    upstreams.ofd_users.clear()
    upstreams.scans.clear()

    trusted_requests = await test_client.send_message("suspicious promo", veteran, group)
    assert not _actions(trusted_requests, RequestType.RESTRICT_CHAT_MEMBER)
    assert not upstreams.ussr_texts and not upstreams.cas_users and not upstreams.ofd_users and not upstreams.scans

    # Disabling trust causes the veteran to be scanned and muted
    off_reply = _text(await test_client.send_command("antispam_trust", admin, args="off", chat=group))
    assert "<b>Trust members after</b>: Off" in off_reply
    untrusted_requests = await test_client.send_message("suspicious promo", veteran, group)
    assert _actions(untrusted_requests, RequestType.RESTRICT_CHAT_MEMBER)
    assert upstreams.ussr_texts == ["suspicious promo"]
