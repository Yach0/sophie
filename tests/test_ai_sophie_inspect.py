from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatType
from beanie import PydanticObjectId
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.exceptions import UsageLimitExceeded

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.modules.ai.fsm.pm import AI_PM_STOP_HELP_TEXT
from sophie_bot.utils.feature_flags import get_default_value
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.modules.ai.utils.sophie_inspect import _parse_chat_ids, is_sophie_inspect_chat, run_sophie_inspect
from sophie_bot.modules.ai.utils.help_tip import build_help_mode_keyboard, should_offer_help_mode
from sophie_bot.modules.ai.utils.sophie_inspect_source import (
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
    window = read_source("modules/ai/utils/sophie_inspect.py", 1)

    assert window is not None
    assert len(window.splitlines()) <= MAX_READ_LINES + 1  # plus the continuation hint


@pytest.mark.parametrize(
    "path",
    ["../pyproject.toml", "../../etc/passwd", "/etc/hostname", "does/not/exist.py", "config.py/../../.env"],
)
def test_reads_outside_sophie_sources_are_refused(path: str) -> None:
    assert read_source(path) is None


def test_only_the_sophie_help_assistant_may_dig_into_sources() -> None:
    assert get_capabilities(AIMode.sophie_help).sophie_inspect
    assert not any(get_capabilities(mode).sophie_inspect for mode in AIMode if mode is not AIMode.sophie_help)


async def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is experimental and costs several model requests, so the flag gates it."""
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.is_enabled", AsyncMock(return_value=False))
    started = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.run_ai_text", started)

    answer = await run_sophie_inspect("how do notes work", PydanticObjectId())

    assert "not available" in answer
    started.assert_not_awaited()


async def test_daily_limit_stops_further_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect._consume_daily_quota", AsyncMock(return_value=False))
    started = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.run_ai_text", started)

    answer = await run_sophie_inspect("how do notes work", PydanticObjectId())

    assert "daily limit" in answer
    started.assert_not_awaited()


async def test_a_run_is_bounded_and_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect._consume_daily_quota", AsyncMock(return_value=True))
    values = {
        "ai_sophie_inspect_model": "cheap/model",
        "ai_sophie_inspect_request_limit": 6,
        "ai_sophie_inspect_tool_calls_limit": 10,
        "ai_sophie_inspect_output_tokens_limit": 700,
    }
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.get_value",
        AsyncMock(side_effect=lambda feature, chat_tid=None: values[feature]),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.get_ai_model",
        lambda model_name: SimpleNamespace(model_name=model_name),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect._build_agent", lambda model: SimpleNamespace())
    run = AsyncMock(return_value=SimpleNamespace(output="Notes are saved with /save.", usage=SimpleNamespace(total_tokens=10)))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.run_ai_text", run)
    charge = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.charge_ai_usage", charge)
    chat_iid = PydanticObjectId()

    answer = await run_sophie_inspect("how do notes work", chat_iid)

    assert answer == "Notes are saved with /save."
    limits = run.await_args.kwargs["usage_limits"]
    assert limits.request_limit > 0
    assert limits.tool_calls_limit > 0
    assert limits.output_tokens_limit > 0
    # The chat pays for its sub-agent out of the same credits as everything else.
    charge.assert_awaited_once()
    assert charge.await_args.args[0] == chat_iid
    assert charge.await_args.args[1] == "sophie_inspect"
    assert charge.await_args.args[2].model_name == "cheap/model"


async def test_the_model_comes_from_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag is only an override; without it the sophie_inspect role decides the model."""
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect._consume_daily_quota", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.get_value",
        AsyncMock(side_effect=lambda feature, chat_tid=None: 8 if "limit" in feature else ""),
    )
    resolve = AsyncMock(return_value="catalog/model")
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.resolve_model_name", resolve)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.get_ai_model", lambda name: SimpleNamespace(model_name=name)
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect._build_agent", lambda model: SimpleNamespace())
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.run_ai_text",
        AsyncMock(return_value=SimpleNamespace(output="answer", usage=SimpleNamespace(total_tokens=5))),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.charge_ai_usage", AsyncMock())

    await run_sophie_inspect("how do notes work", PydanticObjectId())

    assert resolve.await_args.args[1] == AIModelPurpose.sophie_inspect


async def test_running_out_of_budget_does_not_fail_the_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded sub-agent hitting its limit is expected; the user must still get an answer."""
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect._consume_daily_quota", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.get_value",
        AsyncMock(side_effect=lambda feature, chat_tid=None: 8 if "limit" in feature else "cheap/model"),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.get_ai_model", lambda name: SimpleNamespace(model_name=name)
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.sophie_inspect._build_agent", lambda model: SimpleNamespace())
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.run_ai_text",
        AsyncMock(side_effect=UsageLimitExceeded("Exceeded the output_tokens_limit")),
    )

    answer = await run_sophie_inspect("how do notes work", PydanticObjectId())

    assert "could not find the answer" in answer


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("-1001202504432", {-1001202504432}),
        ("-100120, -100999  -100888", {-100120, -100999, -100888}),
        ("", set()),
        ("nonsense", set()),
    ],
)
def test_allowed_chat_ids_are_parsed_leniently(raw_value: str, expected: set[int]) -> None:
    """The flag is a plain string, so a stray separator must not disable the whole list."""
    assert _parse_chat_ids(raw_value) == expected


async def test_a_listed_group_may_use_source_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.sophie_inspect.get_value", AsyncMock(return_value="-1001202504432 -100777")
    )

    assert await is_sophie_inspect_chat(-100777)
    assert not await is_sophie_inspect_chat(-100111)
    assert not await is_sophie_inspect_chat(None)


def _history_with_tool(tool_name: str) -> list:
    return [SimpleNamespace(parts=[ToolCallPart(tool_name=tool_name, args={})])]


async def test_the_help_mode_tip_follows_a_documentation_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.help_tip.is_sophie_inspect_chat", AsyncMock(return_value=False))
    message = SimpleNamespace(chat=SimpleNamespace(id=-100123, type="supergroup"))

    assert await should_offer_help_mode(message, AIMode.support, _history_with_tool("sophie_help"))
    # Nothing to upsell when the answer did not come from the documentation.
    assert not await should_offer_help_mode(message, AIMode.support, _history_with_tool("get_notes"))


async def test_no_tip_where_the_assistant_is_already_the_help_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.help_tip.is_sophie_inspect_chat", AsyncMock(return_value=False))
    message = SimpleNamespace(chat=SimpleNamespace(id=1, type="private"))

    assert not await should_offer_help_mode(message, AIMode.sophie_help, _history_with_tool("sophie_help"))


async def test_no_tip_where_source_inspection_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """That chat already answers more than the documentation, so the tip would be a downgrade."""
    monkeypatch.setattr("sophie_bot.modules.ai.utils.help_tip.is_sophie_inspect_chat", AsyncMock(return_value=True))
    message = SimpleNamespace(chat=SimpleNamespace(id=-1001202504432, type="supergroup"))

    assert not await should_offer_help_mode(message, AIMode.support, _history_with_tool("sophie_help"))


def test_the_tip_button_leads_into_help_mode_from_both_places() -> None:
    private = build_help_mode_keyboard(SimpleNamespace(chat=SimpleNamespace(id=1, type=ChatType.PRIVATE)))
    group = build_help_mode_keyboard(SimpleNamespace(chat=SimpleNamespace(id=-100123, type=ChatType.SUPERGROUP)))

    # A private chat can switch in place; a group has to send the user to the bot.
    assert private.inline_keyboard[0][0].callback_data
    assert group.inline_keyboard[0][0].url.startswith("https://t.me/")


def test_help_mode_prompt_refuses_off_topic_and_names_the_way_out() -> None:
    """Users must not be stuck: refusing without an exit would be a dead end."""
    prompt = get_default_value("ai_help_system_prompt")

    assert "Refuse anything that is not about Sophie" in prompt
    assert "Exit AI help" in prompt
    assert str(AI_PM_STOP_HELP_TEXT).endswith("Exit AI help")


@pytest.mark.usefixtures("db_init")
async def test_a_stale_catalog_row_does_not_stop_the_bot() -> None:
    """The catalog outlives the code that wrote it: an unreadable row costs that row, nothing more."""
    from sophie_bot.modules.ai.utils.ai_catalog import load_catalog
    from sophie_bot.services.db import get_collection

    providers, models = get_collection("ai_catalog_provider"), get_collection("ai_catalog_model")
    await providers.delete_many({})
    await models.delete_many({})
    await providers.insert_one({"name": "openrouter", "kind": "openrouter", "api_key": "k", "enabled": True})
    await models.insert_many(
        [
            # Written by an older version, under a purpose that has since been renamed.
            {"name": "stale/model", "provider": "openrouter", "roles": [{"purpose": "deep_help"}], "enabled": True},
            {"name": "good/model", "provider": "openrouter", "roles": [{"purpose": "chatbot"}], "enabled": True},
        ]
    )

    catalog = await load_catalog()

    assert "good/model" in catalog.models
    assert "stale/model" not in catalog.models
