from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, DisablingModel, FiltersModel, GlobalSettings, RulesModel
from sophie_bot.db.models.communities import CommunityBanModel, CommunityModel, CommunityTask
from sophie_bot.db.models.federations import FederationBan, FederationTask
from sophie_bot.modules.filters.enforce_middleware import EnforceFiltersMiddleware
from tests.e2e.federations.conftest import create_federation_via_command
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id


@pytest.mark.parametrize("spelling", ["set_rules", "setrules", "set-rules", "SET_RULES"])
async def test_rules_routing_preserves_admin_filter_bypass(test_client: TestClient, spelling: str) -> None:
    admin, group, _admin_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    # If the known-command lookup misses a spelling, the filter consumes the command first.
    await FiltersModel(
        chat=chat.iid, handler=spelling.lower(), action=None, actions={"reply": {"text": "caught"}}
    ).insert()

    services = test_client.dispatcher.workflow_data["services"]
    message = MessageFactory.create(text=f"/{spelling} Be kind", from_user=admin, chat=group)
    assert await EnforceFiltersMiddleware()._is_to_drop(message, None, services=services)

    requests = await test_client.send_command(command=spelling, args="Be kind", from_user=admin, chat=group)

    rules = await RulesModel.get_rules(chat.iid)
    assert rules is not None and rules.text == "Be kind"
    assert requests
    assert not any("caught" in (request.text or "") for request in requests)


@pytest.mark.parametrize("spelling", ["set_rules", "setrules", "set-rules", "SET_RULES"])
@pytest.mark.parametrize("other_bot", [False, True])
async def test_admin_command_mention_preserves_filter_enforcement(
    test_client: TestClient, spelling: str, other_bot: bool
) -> None:
    admin, group, _admin_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await FiltersModel(
        chat=chat.iid, handler=spelling.lower(), action=None, actions={"reply": {"text": "caught"}}
    ).insert()

    services = test_client.dispatcher.workflow_data["services"]
    bot_user = await services.bot.me()
    assert bot_user.username is not None
    mention = "AnotherBot" if other_bot else bot_user.username.swapcase()
    text = f"/{spelling}@{mention} Be kind"
    message = MessageFactory.create(text=text, from_user=admin, chat=group)
    assert await EnforceFiltersMiddleware()._is_to_drop(message, None, services=services) is not other_bot

    requests = await test_client.send_message(text=text, from_user=admin, chat=group)

    assert any("caught" in (request.text or "") for request in requests) is other_bot
    rules = await RulesModel.get_rules(chat.iid)
    if other_bot:
        assert rules is None
    else:
        assert rules is not None and rules.text == "Be kind"


@pytest.mark.parametrize("spelling", ["note_list", "notelist", "note-list", "NOTE_LIST"])
async def test_disable_enable_alias_uses_stable_storage_key(test_client: TestClient, spelling: str) -> None:
    admin, group, _admin_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None

    requests = await test_client.send_command(command="disable", args=spelling, from_user=admin, chat=group)
    assert await DisablingModel.get_disabled(chat.iid) == ["notes"]
    assert any("/notes" in (request.text or "") for request in requests)

    await test_client.send_command(command="enable", args=spelling, from_user=admin, chat=group)
    assert await DisablingModel.get_disabled(chat.iid) == []


@pytest.mark.parametrize("spelling", ["set_rules", "setrules", "set-rules"])
async def test_canonical_command_still_requires_admin(test_client: TestClient, spelling: str) -> None:
    member, group, _member_model = await create_test_user_and_group(test_client)

    requests = await test_client.send_command(command=spelling, args="Unauthorized", from_user=member, chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    assert await RulesModel.get_rules(chat.iid) is None
    assert any("administrator" in (request.text or "").lower() for request in requests)


@pytest.mark.parametrize(
    ("spelling", "silent"),
    [("sfban", True), ("s_fban", True), ("S-FBAN", True), ("fban", False), ("F-BAN", False), ("F_BAN", False)],
)
async def test_federation_ban_registered_identity_preserves_silent_behavior(
    test_client: TestClient,
    spelling: str,
    silent: bool,
) -> None:
    owner, group, owner_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, owner.id, creator=True)
    await grant_bot_admin(group.id)
    target = test_client.create_user(user_id=next_user_id(), first_name="Target")
    await test_client.send_message(text="init", from_user=target.user, chat=group)
    federation = await create_federation_via_command(test_client, owner, group, "Canonical Fed", owner_model)
    await test_client.send_command(command="join_fed", args=federation.fed_id, from_user=owner, chat=group)
    test_client.capture.clear()

    await test_client.send_command(command=spelling, args=f"{target.user.id} reason", from_user=owner, chat=group)

    ban = await FederationBan.find_one(FederationBan.user_id == target.user.id)
    assert ban is not None
    task = await FederationTask.find_one(FederationTask.target_user_id == target.user.id)
    assert task is not None and task.silent is silent
    assert test_client.capture.get_by_type(RequestType.BAN_CHAT_MEMBER)


@pytest.mark.parametrize(
    ("spelling", "silent"),
    [("scban", True), ("s_cban", True), ("S-CBAN", True), ("cban", False), ("C-BAN", False), ("C_BAN", False)],
)
async def test_community_ban_registered_identity_preserves_silent_behavior(
    test_client: TestClient,
    spelling: str,
    silent: bool,
) -> None:
    admin, group, _admin_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    community = await CommunityModel.ensure_community(12345, "Canonical Community")
    await chat.set_community(community.community_tid)
    target = test_client.create_user(user_id=next_user_id(), first_name="Target")
    await test_client.send_message(text="init", from_user=target.user, chat=group)
    test_client.capture.clear()

    await test_client.send_command(command=spelling, args=f"{target.user.id} reason", from_user=admin, chat=group)

    ban = await CommunityBanModel.find_one(CommunityBanModel.user_id == target.user.id)
    assert ban is not None
    task = await CommunityTask.find_one(CommunityTask.target_user_id == target.user.id)
    assert task is not None and task.silent is silent
    assert test_client.capture.get_by_type(RequestType.BAN_CHAT_MEMBER)


@pytest.mark.parametrize("spelling", ["op_set_beta", "op_setbeta", "op-set-beta", "OP_SET_BETA"])
@pytest.mark.parametrize("operator", [True, False])
async def test_op_set_beta_spelling_preserves_operator_permissions(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    spelling: str,
    operator: bool,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    monkeypatch.setattr(CONFIG, "operators", [user.id] if operator else [])

    requests = await test_client.send_command(command=spelling, args="37", from_user=user, chat=group)

    setting = await GlobalSettings.get_by_key("beta_percentage")
    if operator:
        assert setting is not None and setting.value == 37
        assert any("37%" in (request.text or "") for request in requests)
    else:
        assert setting is None


@pytest.mark.parametrize("spelling", ["adminlist", "admin_list", "ADMIN-LIST"])
async def test_enable_resolves_legacy_db_key_after_command_rename(test_client: TestClient, spelling: str) -> None:
    admin, group, _admin_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, admin.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await DisablingModel(chat=chat.iid, cmds=["adminlist"]).insert()

    requests = await test_client.send_command(command="disabled", from_user=admin, chat=group)
    assert any("/admin_list" in (request.text or "") for request in requests)
    assert await DisablingModel.get_disabled(chat.iid) == ["adminlist"]

    requests = await test_client.send_command(command="enable", args=spelling, from_user=admin, chat=group)
    assert any("Command enabled" in (request.text or "") for request in requests)
    assert await DisablingModel.get_disabled(chat.iid) == []
