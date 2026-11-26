from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from sophie_bot.db.models.antispam import AntispamMode, AntispamPolicy
from sophie_bot.modules.antispam import service
from sophie_bot.modules.antispam.provider import (
    ProtectionUnavailable,
    ProtectionVerdict,
    ProviderCapability,
    ProviderOrigin,
)


@pytest.mark.parametrize("exemption", ["admin", "whitelist", "excluded", "sender_chat", "bot_added_by_admin", None])
async def test_exemptions_skip_upstream_and_only_whitelist_is_audited(
    exemption: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member = User(id=101, first_name="Member", is_bot=exemption == "bot_added_by_admin")
    sender = User(id=102, first_name="Sender", is_bot=False)
    group = Chat(id=-1001234, type="supergroup")
    message = Message(
        message_id=1,
        chat=group,
        date=datetime.now(UTC),
        from_user=sender,
        sender_chat=group if exemption == "sender_chat" else None,
    )
    chat = SimpleNamespace(iid="chat_iid", tid=group.id)
    services = SimpleNamespace(bot=SimpleNamespace(id=999), redis=object())
    is_admin = AsyncMock(
        side_effect=lambda _chat, user: (
            (exemption == "admin" and user == member.id) or (exemption == "bot_added_by_admin" and user == sender.id)
        ),
    )
    whitelist = AsyncMock(return_value=exemption == "whitelist")
    excluded = AsyncMock(return_value=exemption == "excluded")
    audit = AsyncMock()
    monkeypatch.setattr(service, "is_user_admin", is_admin)
    monkeypatch.setattr(service, "is_user_group_whitelisted", whitelist)
    monkeypatch.setattr(service, "log_group_whitelist_exemption", audit)
    monkeypatch.setattr(service.AntispamExclusionModel, "is_excluded", excluded)

    result = await service.exempt(chat, member, services, message=message, joining=True)
    assert result is (exemption is not None)
    if exemption == "whitelist":
        audit.assert_awaited_once_with(group.id, member.id, "antispam")
    else:
        audit.assert_not_awaited()
    if exemption in {"admin", "sender_chat", "bot_added_by_admin"}:
        whitelist.assert_not_awaited()
        excluded.assert_not_awaited()


async def test_evaluation_keeps_a_match_when_other_sources_are_clean_or_unavailable() -> None:
    outage = SimpleNamespace(
        origin=ProviderOrigin.THIRD_PARTY,
        capabilities=ProviderCapability.USER_LOOKUP,
        check_user=AsyncMock(side_effect=ProtectionUnavailable),
    )
    clean = SimpleNamespace(
        origin=ProviderOrigin.THIRD_PARTY,
        capabilities=ProviderCapability.USER_LOOKUP,
        check_user=AsyncMock(return_value=ProtectionVerdict("clean", False)),
    )
    matched = SimpleNamespace(
        origin=ProviderOrigin.THIRD_PARTY,
        capabilities=ProviderCapability.USER_LOOKUP,
        check_user=AsyncMock(return_value=ProtectionVerdict("matched", True)),
    )
    protection = service.AntispamService(
        SimpleNamespace(),
        SimpleNamespace(),
        AntispamPolicy(mode=AntispamMode.STRICT),
        (outage, clean, matched),
    )
    verdicts = await protection._evaluate(123, message=None, membership=None, joining=True, join_request=True)
    assert [match.verdict.source_id for match in verdicts] == ["matched"]
    for provider in (outage, clean, matched):
        provider.check_user.assert_awaited_once_with(123)


@pytest.mark.parametrize(
    ("age_days", "threshold_days", "expected"),
    [
        (31, 30, True),
        (3, 3, True),
        (2, 3, False),
        (365, None, False),
    ],
)
def test_trusted_member_age_threshold(age_days: int, threshold_days: int | None, expected: bool) -> None:
    membership = SimpleNamespace(first_saw=datetime.now(UTC) - timedelta(days=age_days, minutes=1))
    threshold_seconds = threshold_days * 86400 if threshold_days is not None else None
    assert service.is_trusted_member(membership, threshold_seconds) is expected
