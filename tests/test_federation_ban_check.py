from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId
from bson import DBRef

from sophie_bot.utils import federation_ban_check
from sophie_bot.utils.federation_ban_check import (
    FederationBanInfo,
    _get_federation_for_chat,
    _get_subscription_chain,
    _normalize_chat_iids,
    get_user_federation_ban_info,
)


class FakeField:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: object) -> tuple[str, object]:
        return self.name, other


@pytest.fixture(autouse=True)
def patch_model_query_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(federation_ban_check.Federation, "chats", FakeField("chats"), raising=False)
    monkeypatch.setattr(federation_ban_check.Federation, "fed_id", FakeField("fed_id"), raising=False)
    monkeypatch.setattr(federation_ban_check.FederationBan, "fed_id", FakeField("fed_id"), raising=False)
    monkeypatch.setattr(federation_ban_check.FederationBan, "user_id", FakeField("user_id"), raising=False)


class FakeBanQuery:
    def __init__(self, ban: SimpleNamespace | None) -> None:
        self.ban = ban

    async def first_or_none(self) -> SimpleNamespace | None:
        return self.ban


class FakeFederationCursor:
    def __init__(self, federations: list[SimpleNamespace]) -> None:
        self.federations = federations

    def __aiter__(self) -> "FakeFederationCursor":
        self.index = 0
        return self

    async def __anext__(self) -> SimpleNamespace:
        if self.index >= len(self.federations):
            raise StopAsyncIteration
        federation = self.federations[self.index]
        self.index += 1
        return federation


class FakeChatLink:
    def __init__(self, reference: object) -> None:
        self.reference = reference

    def to_ref(self) -> object:
        return self.reference


def test_normalize_chat_iids_accepts_object_ids_dbrefs_and_link_dicts() -> None:
    direct_iid = PydanticObjectId()
    dbref_iid = PydanticObjectId()
    dict_iid = PydanticObjectId()

    normalized = _normalize_chat_iids(
        [
            direct_iid,
            DBRef("chats", dbref_iid),
            {"$id": dict_iid},
            {"$id": "not-an-object-id"},
            object(),
        ]
    )

    assert normalized == [direct_iid, dbref_iid, dict_iid]


@pytest.mark.asyncio
async def test_get_federation_for_chat_returns_direct_match(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    federation = SimpleNamespace(fed_id="fed-1")

    async def fake_find_one(condition: object) -> SimpleNamespace:
        return federation

    monkeypatch.setattr(federation_ban_check.Federation, "find_one", fake_find_one)

    assert await _get_federation_for_chat(chat_iid) is federation


@pytest.mark.asyncio
async def test_get_federation_for_chat_falls_back_to_normalized_chat_links(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    matching_federation = SimpleNamespace(chats=[FakeChatLink(DBRef("chats", chat_iid))])
    empty_federation = SimpleNamespace(chats=[])

    async def fake_find_one(condition: object) -> None:
        return None

    def fake_find_all() -> FakeFederationCursor:
        return FakeFederationCursor([empty_federation, matching_federation])

    monkeypatch.setattr(federation_ban_check.Federation, "find_one", fake_find_one)
    monkeypatch.setattr(federation_ban_check.Federation, "find_all", fake_find_all)

    assert await _get_federation_for_chat(chat_iid) is matching_federation


@pytest.mark.asyncio
async def test_get_subscription_chain_walks_subscribed_federations_without_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    federations = {
        "root": SimpleNamespace(fed_id="root", subscribed=["child", "sibling"]),
        "child": SimpleNamespace(fed_id="child", subscribed=["root", "leaf"]),
        "sibling": SimpleNamespace(fed_id="sibling", subscribed=[]),
        "leaf": SimpleNamespace(fed_id="leaf", subscribed=[]),
    }

    async def fake_find_one(condition: object) -> SimpleNamespace | None:
        current_fed_id = pending_find_ids.pop(0)
        return federations.get(current_fed_id)

    pending_find_ids = ["root", "sibling", "child", "leaf"]
    monkeypatch.setattr(federation_ban_check.Federation, "find_one", fake_find_one)

    assert await _get_subscription_chain("root") == ["child", "sibling", "leaf"]


@pytest.mark.asyncio
async def test_get_subscription_chain_skips_missing_or_unsubscribed_federations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_find_one(condition: object) -> None:
        return None

    monkeypatch.setattr(federation_ban_check.Federation, "find_one", fake_find_one)

    assert await _get_subscription_chain("missing") == []


@pytest.mark.asyncio
async def test_get_user_federation_ban_info_returns_none_without_federation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_federation_for_chat(chat_iid: PydanticObjectId) -> None:
        return None

    monkeypatch.setattr(federation_ban_check, "_get_federation_for_chat", fake_get_federation_for_chat)

    assert await get_user_federation_ban_info(PydanticObjectId(), 42) is None


@pytest.mark.asyncio
async def test_get_user_federation_ban_info_returns_current_federation_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    federation = SimpleNamespace(fed_id="current", fed_name="Current Federation")

    async def fake_get_federation_for_chat(chat_iid: PydanticObjectId) -> SimpleNamespace:
        return federation

    async def fake_get_subscription_chain(fed_id: str) -> list[str]:
        return ["subscribed"]

    def fake_find(*conditions: object) -> FakeBanQuery:
        return FakeBanQuery(SimpleNamespace(fed_id="current"))

    monkeypatch.setattr(federation_ban_check, "_get_federation_for_chat", fake_get_federation_for_chat)
    monkeypatch.setattr(federation_ban_check, "_get_subscription_chain", fake_get_subscription_chain)
    monkeypatch.setattr(federation_ban_check.FederationBan, "find", fake_find)

    assert await get_user_federation_ban_info(PydanticObjectId(), 42) == FederationBanInfo(
        scope="current",
        fed_name="Current Federation",
        fed_id="current",
    )


@pytest.mark.asyncio
async def test_get_user_federation_ban_info_returns_subscribed_federation_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    federation = SimpleNamespace(fed_id="current", fed_name="Current Federation")
    banning_federation = SimpleNamespace(fed_id="subscribed", fed_name="Subscribed Federation")

    async def fake_get_federation_for_chat(chat_iid: PydanticObjectId) -> SimpleNamespace:
        return federation

    async def fake_get_subscription_chain(fed_id: str) -> list[str]:
        return ["subscribed"]

    def fake_find(*conditions: object) -> FakeBanQuery:
        return FakeBanQuery(SimpleNamespace(fed_id="subscribed"))

    async def fake_find_one(condition: object) -> SimpleNamespace:
        return banning_federation

    monkeypatch.setattr(federation_ban_check, "_get_federation_for_chat", fake_get_federation_for_chat)
    monkeypatch.setattr(federation_ban_check, "_get_subscription_chain", fake_get_subscription_chain)
    monkeypatch.setattr(federation_ban_check.FederationBan, "find", fake_find)
    monkeypatch.setattr(federation_ban_check.Federation, "find_one", fake_find_one)

    assert await get_user_federation_ban_info(PydanticObjectId(), 42) == FederationBanInfo(
        scope="subscribed",
        fed_name="Subscribed Federation",
        fed_id="subscribed",
    )


@pytest.mark.asyncio
async def test_get_user_federation_ban_info_uses_fed_id_when_subscribed_federation_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    federation = SimpleNamespace(fed_id="current", fed_name="Current Federation")

    async def fake_get_federation_for_chat(chat_iid: PydanticObjectId) -> SimpleNamespace:
        return federation

    async def fake_get_subscription_chain(fed_id: str) -> list[str]:
        return ["missing"]

    def fake_find(*conditions: object) -> FakeBanQuery:
        return FakeBanQuery(SimpleNamespace(fed_id="missing"))

    async def fake_find_one(condition: object) -> None:
        return None

    monkeypatch.setattr(federation_ban_check, "_get_federation_for_chat", fake_get_federation_for_chat)
    monkeypatch.setattr(federation_ban_check, "_get_subscription_chain", fake_get_subscription_chain)
    monkeypatch.setattr(federation_ban_check.FederationBan, "find", fake_find)
    monkeypatch.setattr(federation_ban_check.Federation, "find_one", fake_find_one)

    assert await get_user_federation_ban_info(PydanticObjectId(), 42) == FederationBanInfo(
        scope="subscribed",
        fed_name="missing",
        fed_id="missing",
    )
