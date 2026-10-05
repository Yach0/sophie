from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiogram import Router
from beanie import PydanticObjectId
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from sophie_bot.config import CONFIG
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules import LoadedModuleRegistry
from sophie_bot.modules.ai.agent_tools.sophie_help import sophie_help
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.help.utils.extract_info import ModuleHelp, gather_cmds_help
from sophie_bot.services.application import ApplicationServices


async def _callback() -> None:
    pass


def _context(registry: LoadedModuleRegistry, user_tid: int = 1002) -> RunContext[SophieAIToolContext]:
    services = MagicMock(spec=ApplicationServices)
    services.modules = registry
    return RunContext(
        deps=SophieAIToolContext(
            connection=MagicMock(spec=ChatConnection),
            chat_tid=-100123,
            chat_iid=PydanticObjectId(),
            services=services,
            user_tid=user_tid,
        ),
        model=TestModel(),
        usage=RunUsage(),
    )


async def _module(router: Router, *, exclude_public: bool = False) -> ModuleHelp:
    return ModuleHelp(
        handlers=await gather_cmds_help(router, {"ai_chatbot": False}, {}),
        name=router.name or "Module",
        icon="🔧",
        exclude_public=exclude_public,
        info="Useful module information",
        description="Useful module description",
        advertise_wiki_page=True,
    )


@pytest.mark.parametrize("user_tid", [1001, 1002], ids=["operator", "regular-user"])
async def test_catalog_excludes_op_handlers_and_all_their_aliases(
    monkeypatch: pytest.MonkeyPatch, user_tid: int
) -> None:
    monkeypatch.setattr(CONFIG, "operators", [1001])
    router = Router(name="mixed")
    nested_router = Router(name="nested")
    nested_router.message.register(
        _callback,
        CMDFilter(("op_secret", "quota")),
        IsOP(True),
        flags={"help": {"description": "Private quota controls", "alias_to_modules": ["notes"]}},
    )
    router.include_router(nested_router)
    router.message.register(
        _callback,
        CMDFilter(("aiusage", "usage", "op_public_alias")),
        flags={"help": {"description": "Check remaining quota", "alias_to_modules": ["notes"]}},
    )
    router.message.register(_callback, CMDFilter("op_ai_model"), IsOP(True))
    registry = LoadedModuleRegistry()
    module = await _module(router)
    registry.help_modules["mixed"] = module
    assert [handler.only_op for handler in module.handlers] == [True, False, True]
    assert module.handlers[0].cmds == ("op_secret", "quota")

    text = await sophie_help(_context(registry, user_tid))

    assert "/op_secret" not in text
    assert "/quota" not in text
    assert "Private quota controls" not in text
    assert "/op_ai_model" not in text
    assert "/aiusage / /usage / /op_public_alias" in text
    assert "Check remaining quota" in text
    assert "Useful module information" in text
    assert "Useful module description" in text
    assert len(module.handlers) == 3  # Visibility must not mutate the shared catalog.


async def test_catalog_excludes_entire_nonpublic_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "operators", [1001])
    hidden_router = Router(name="Operator tools")
    hidden_router.message.register(_callback, CMDFilter(("secret_stats", "stats")), IsOP(True))
    hidden_router.message.register(_callback, CMDFilter("internal_preview"))
    public_router = Router(name="Notes")
    public_router.message.register(_callback, CMDFilter("notes"))
    registry = LoadedModuleRegistry()
    registry.help_modules["op"] = await _module(hidden_router, exclude_public=True)
    registry.help_modules["notes"] = await _module(public_router)

    text = await sophie_help(_context(registry, user_tid=1001))

    assert "Operator tools" not in text
    assert CONFIG.wiki_modules_link + "op" not in text
    assert "/secret_stats" not in text
    assert "/stats" not in text
    assert "/internal_preview" not in text
    assert "/notes" in text


async def test_catalog_preserves_feature_and_help_exclusions() -> None:
    router = Router(name="Features")
    router.message.register(_callback, CMDFilter("disabled_ai"), FeatureFlagFilter("ai_chatbot"))
    router.message.register(_callback, CMDFilter("fallback_ai"), FeatureFlagFilter("ai_chatbot", enabled=False))
    router.message.register(_callback, CMDFilter("hidden_help"), flags={"help": {"exclude": True}})
    registry = LoadedModuleRegistry()
    registry.help_modules["features"] = await _module(router)

    text = await sophie_help(_context(registry))

    assert "/disabled_ai" not in text
    assert "/hidden_help" not in text
    assert "/fallback_ai" in text


async def test_empty_catalog_keeps_existing_response() -> None:
    assert await sophie_help(_context(LoadedModuleRegistry())) == "No modules found."


async def test_wiki_page_reading_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sophie_bot.modules.ai.agent_tools.sophie_help.read_wiki_page",
        lambda page: "Full wiki text" if page == "notes" else None,
    )
    assert await sophie_help(_context(LoadedModuleRegistry()), page="notes") == "Full wiki text"
