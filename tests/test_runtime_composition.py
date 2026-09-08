from __future__ import annotations

from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher, Router
from fakeredis import FakeAsyncRedis
from fastapi import APIRouter, FastAPI

from sophie_bot import modules as module_loader
from sophie_bot.modules import (
    LoadedModuleRegistry,
    ModuleManifest,
    assemble_api_modules,
    assemble_bot_modules,
    discover_modules,
    initialize_modules,
)
from sophie_bot.runtime import build_rest_runtime, build_scheduler_runtime
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.db import DatabaseResources


def _module(name: str, manifest: ModuleManifest) -> ModuleType:
    module = ModuleType(f"sophie_bot.modules.{name}")
    module.module_manifest = manifest
    return module


def _services(registry: LoadedModuleRegistry) -> ApplicationServices:
    bot = cast(Bot, SimpleNamespace(session=SimpleNamespace(close=AsyncMock())))
    redis = FakeAsyncRedis(decode_responses=False)
    return cast(
        ApplicationServices,
        SimpleNamespace(
            bot=bot,
            redis=redis,
            modules=registry,
        ),
    )


def test_discovery_only_imports_selected_manifests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialized: list[str] = []

    async def initialize(_services: ApplicationServices) -> None:
        initialized.append("alpha")

    alpha = _module("alpha", ModuleManifest(name="alpha", initialize=initialize))
    beta = _module("beta", ModuleManifest(name="beta"))
    modules = {
        "sophie_bot.modules.alpha": alpha,
        "sophie_bot.modules.beta": beta,
    }
    monkeypatch.setattr(module_loader, "MODULES", ["alpha", "beta"])
    monkeypatch.setattr(module_loader, "import_module", modules.__getitem__)

    registry = discover_modules(["*"], ["beta"])

    assert registry.modules == {"alpha": alpha}
    assert initialized == []


@pytest.mark.asyncio
async def test_module_hooks_and_transports_use_runtime_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeHandler:
        @classmethod
        def register(cls, router: Router) -> None:
            calls.append(f"handler:{router.name}")

    async def initialize(_services: ApplicationServices) -> None:
        calls.append("initialize")

    async def setup_bot(
        router: Router,
        _services: ApplicationServices,
    ) -> None:
        calls.append(f"setup:{router.name}")

    api_router = APIRouter()

    @api_router.get("/alpha")
    async def alpha_endpoint() -> dict[str, str]:
        return {"status": "alpha"}

    alpha = _module(
        "alpha",
        ModuleManifest(
            name="alpha",
            bot_router_factory=lambda: Router(name="alpha"),
            api_router_factory=lambda: api_router,
            handlers=(FakeHandler,),
            initialize=initialize,
            setup_bot=setup_bot,
        ),
    )
    monkeypatch.setattr(module_loader, "MODULES", ["alpha"])
    monkeypatch.setattr(module_loader, "import_module", lambda _path: alpha)
    registry = discover_modules(["*"])
    services = _services(registry)
    dispatcher = Dispatcher()
    app = FastAPI()

    await initialize_modules(services)
    await assemble_bot_modules(dispatcher, services)
    assemble_api_modules(app, registry)
    route_count = len(app.routes)
    assemble_api_modules(app, registry)

    assert calls == ["initialize", "handler:alpha", "setup:alpha"]
    assert app.url_path_for("alpha_endpoint") == "/alpha"
    assert len(app.routes) == route_count
    await services.redis.aclose()


def test_discovery_requires_explicit_matching_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = ModuleType("sophie_bot.modules.legacy")
    monkeypatch.setattr(module_loader, "MODULES", ["legacy"])
    monkeypatch.setattr(module_loader, "import_module", lambda _path: legacy)
    with pytest.raises(RuntimeError, match="must export module_manifest"):
        discover_modules(["*"])

    mismatched = _module("legacy", ModuleManifest(name="other"))
    monkeypatch.setattr(module_loader, "import_module", lambda _path: mismatched)
    with pytest.raises(RuntimeError, match="must match configured name"):
        discover_modules(["*"])


@pytest.mark.asyncio
async def test_non_bot_runtimes_never_construct_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
    db_init: Any,
) -> None:
    created_bots: list[Any] = []

    def create_bot(_config: Any) -> Bot:
        bot = cast(Bot, SimpleNamespace(session=SimpleNamespace(close=AsyncMock())))
        created_bots.append(bot)
        return bot

    redis_clients: list[FakeAsyncRedis] = []

    def create_redis(_config: Any) -> FakeAsyncRedis:
        redis = FakeAsyncRedis(decode_responses=False)
        redis_clients.append(redis)
        return redis

    borrowed_database = DatabaseResources(
        mongo=cast(Any, SimpleNamespace(close=AsyncMock())),
        database=db_init,
        initialized=True,
    )
    monkeypatch.setattr("sophie_bot.runtime.create_bot", create_bot)
    monkeypatch.setattr("sophie_bot.runtime.create_redis", create_redis)
    monkeypatch.setattr(
        "sophie_bot.runtime.discover_modules",
        lambda *_args: LoadedModuleRegistry(),
    )
    monkeypatch.setattr(
        "sophie_bot.runtime.create_dispatcher",
        lambda *_args: (_ for _ in ()).throw(AssertionError("dispatcher constructed")),
    )
    monkeypatch.setattr(
        "sophie_bot.runtime.create_scheduler",
        lambda *_args: SimpleNamespace(name="scheduler"),
    )

    app = FastAPI()
    async with build_rest_runtime(app, database=borrowed_database) as rest_runtime:
        assert app.state.services is rest_runtime.services
    assert app.state.services is None

    async with build_scheduler_runtime(database=borrowed_database) as scheduler_runtime:
        assert scheduler_runtime.scheduler.name == "scheduler"

    assert borrowed_database.mongo.close.await_count == 0
    assert len(created_bots) == 2
    assert all(bot.session.close.await_count == 1 for bot in created_bots)
    assert len(redis_clients) == 2
