from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sophie_bot.modules.antispam import ussr
from sophie_bot.modules.antispam.provider import ProtectionUnavailable, ThreatCategory


@pytest.mark.parametrize("probability,expected_match", [(0.9, True), (0.2, False)])
async def test_ussr_provider_persists_matches_and_returns_verdict(
    monkeypatch: pytest.MonkeyPatch,
    probability: float,
    expected_match: bool,
) -> None:
    predictor = MagicMock(return_value={"spam_probability": probability, "nsfw_probability": 0.0})
    match_doc = MagicMock(insert=AsyncMock())
    model = MagicMock(return_value=match_doc)
    monkeypatch.setattr(ussr, "predict_message", predictor)
    monkeypatch.setattr(ussr, "SpamMatchModel", model)

    verdict = await ussr.UssrProvider().scan_content("buy now", 123, chat_tid=-100123)

    predictor.assert_called_once_with("buy now")
    assert verdict.source_id == "ussr"
    assert verdict.matched is expected_match
    assert verdict.risk_score == probability
    if expected_match:
        assert verdict.categories == (ThreatCategory.SPAM,)
        model.assert_called_once_with(text="buy now", spam_probability=probability, nsfw_probability=0.0, chat_tid=-100123)
        match_doc.insert.assert_awaited_once()
    else:
        assert verdict.categories == ()
        model.assert_not_called()


async def test_ussr_provider_fails_open_when_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ussr, "predict_message", MagicMock(side_effect=FileNotFoundError("missing")))
    with pytest.raises(ProtectionUnavailable):
        await ussr.UssrProvider().scan_content("buy now", 123, chat_tid=-100123)
