from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId
from pydantic_ai.exceptions import UsageLimitExceeded

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.modules.ai.utils.deep_help import run_deep_help
from sophie_bot.modules.ai.utils.deep_help_source import (
    MAX_READ_LINES,
    MAX_SEARCH_MATCHES,
    read_source,
    search_source,
)


def test_search_is_capped() -> None:
    """An unbounded search would let one sub-agent run pull the whole codebase into its context."""
    matches = search_source("def ")

    assert len(matches) == MAX_SEARCH_MATCHES


def test_read_window_is_capped() -> None:
    window = read_source("modules/ai/utils/deep_help.py", 1)

    assert window is not None
    assert len(window.splitlines()) <= MAX_READ_LINES + 1  # plus the continuation hint


@pytest.mark.parametrize(
    "path",
    ["../pyproject.toml", "../../etc/passwd", "/etc/hostname", "does/not/exist.py", "config.py/../../.env"],
)
def test_reads_outside_sophie_sources_are_refused(path: str) -> None:
    assert read_source(path) is None


def test_only_the_sophie_help_assistant_may_dig_into_sources() -> None:
    assert get_capabilities(AIMode.sophie_help).deep_help
    assert not any(get_capabilities(mode).deep_help for mode in AIMode if mode is not AIMode.sophie_help)


async def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is experimental and costs several model requests, so the flag gates it."""
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.is_enabled", AsyncMock(return_value=False))
    started = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.run_ai_text", started)

    answer = await run_deep_help("how do notes work", PydanticObjectId())

    assert "not available" in answer
    started.assert_not_awaited()


async def test_daily_limit_stops_further_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help._consume_daily_quota", AsyncMock(return_value=False))
    started = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.run_ai_text", started)

    answer = await run_deep_help("how do notes work", PydanticObjectId())

    assert "daily limit" in answer
    started.assert_not_awaited()


async def test_a_run_is_bounded_and_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help._consume_daily_quota", AsyncMock(return_value=True))
    values = {
        "ai_deep_help_model": "cheap/model",
        "ai_deep_help_request_limit": 6,
        "ai_deep_help_tool_calls_limit": 10,
        "ai_deep_help_output_tokens_limit": 700,
    }
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.deep_help.get_value",
        AsyncMock(side_effect=lambda feature, chat_tid=None: values[feature]),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.deep_help.get_ai_model",
        lambda model_name: SimpleNamespace(model_name=model_name),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help._build_agent", lambda model: SimpleNamespace())
    run = AsyncMock(return_value=SimpleNamespace(output="Notes are saved with /save.", usage=SimpleNamespace(total_tokens=10)))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.run_ai_text", run)
    charge = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.charge_ai_usage", charge)
    chat_iid = PydanticObjectId()

    answer = await run_deep_help("how do notes work", chat_iid)

    assert answer == "Notes are saved with /save."
    limits = run.await_args.kwargs["usage_limits"]
    assert limits.request_limit > 0
    assert limits.tool_calls_limit > 0
    assert limits.output_tokens_limit > 0
    # The chat pays for its sub-agent out of the same credits as everything else.
    charge.assert_awaited_once()
    assert charge.await_args.args[0] == chat_iid
    assert charge.await_args.args[1] == "deep_help"
    assert charge.await_args.args[2].model_name == "cheap/model"


async def test_running_out_of_budget_does_not_fail_the_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded sub-agent hitting its limit is expected; the user must still get an answer."""
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help._consume_daily_quota", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.deep_help.get_value",
        AsyncMock(side_effect=lambda feature, chat_tid=None: 8 if "limit" in feature else "cheap/model"),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.deep_help.get_ai_model", lambda name: SimpleNamespace(model_name=name)
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.deep_help._build_agent", lambda model: SimpleNamespace())
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.deep_help.run_ai_text",
        AsyncMock(side_effect=UsageLimitExceeded("Exceeded the output_tokens_limit")),
    )

    answer = await run_deep_help("how do notes work", PydanticObjectId())

    assert "could not find the answer" in answer
