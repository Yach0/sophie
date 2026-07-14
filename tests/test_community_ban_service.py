"""Unit tests for CommunityBanService eligibility validation."""

from __future__ import annotations

import pytest

from sophie_bot.config import CONFIG
from sophie_bot.modules.communities.exceptions import CommunityBanValidationError
from sophie_bot.modules.communities.services import CommunityBanService


def test_validate_allows_regular_user() -> None:
    # Should not raise for an ordinary target/banner pair.
    CommunityBanService.validate_ban_eligibility(target_user_tid=111, banner_user_tid=222)


def test_validate_blocks_self_ban() -> None:
    with pytest.raises(CommunityBanValidationError):
        CommunityBanService.validate_ban_eligibility(target_user_tid=111, banner_user_tid=111)


def test_validate_blocks_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "operators", [999])
    with pytest.raises(CommunityBanValidationError):
        CommunityBanService.validate_ban_eligibility(target_user_tid=999, banner_user_tid=222)


def test_validate_blocks_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    # bot_id is a property on the Config class, so patch it there.
    monkeypatch.setattr(type(CONFIG), "bot_id", property(lambda _self: 42))
    with pytest.raises(CommunityBanValidationError):
        CommunityBanService.validate_ban_eligibility(target_user_tid=42, banner_user_tid=222)
