from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest
from aiogram import Dispatcher, Router
from fastapi import APIRouter, FastAPI

from sophie_bot import modules as module_loader
from sophie_bot.modules import LoadedModuleRegistry, get_loaded_module_registry
from sophie_bot.runtime import build_rest_runtime, build_scheduler_runtime
from sophie_bot.services.rest import init_api_routers


def test_build_rest_runtime_sets_runtime_local_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_app = FastAPI()
    fake_bot_runtime = SimpleNamespace(name="bot-runtime")

    monkeypatch.setattr("sophie_bot.runtime.create_app", lambda: fake_app)
    monkeypatch.setattr("sophie_bot.runtime.create_bot_runtime", lambda: fake_bot_runtime)
    monkeypatch.setattr("sophie_bot.runtime.set_bot_runtime", lambda runtime: runtime)

    runtime = build_rest_runtime()

    assert runtime.app is fake_app
    assert runtime.bot_runtime is fake_bot_runtime
    assert get_loaded_module_registry() is runtime.loaded_modules
    assert runtime.loaded_modules.modules == {}
    assert runtime.loaded_modules.api_routers == []


def test_build_scheduler_runtime_sets_runtime_local_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bot_runtime = SimpleNamespace(name="bot-runtime")
    fake_scheduler = SimpleNamespace(name="scheduler")

    monkeypatch.setattr("sophie_bot.runtime.create_bot_runtime", lambda: fake_bot_runtime)
    monkeypatch.setattr("sophie_bot.runtime.set_bot_runtime", lambda runtime: runtime)
    monkeypatch.setattr("sophie_bot.runtime.create_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr("sophie_bot.runtime.set_scheduler", lambda scheduler: scheduler)

    runtime = build_scheduler_runtime()

    assert runtime.bot_runtime is fake_bot_runtime
    assert runtime.scheduler is fake_scheduler
    assert get_loaded_module_registry() is runtime.loaded_modules


@pytest.mark.asyncio
async def test_load_modules_uses_runtime_local_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    handler_calls: list[str] = []
    pre_setup_calls: list[str] = []
    post_setup_calls: list[dict[str, ModuleType]] = []

    alpha_router = Router(name="alpha")
    alpha_api_router = APIRouter()
    beta_router = Router(name="beta")

    class FakeHandler:
        @classmethod
        def register(cls, router: Router) -> None:
            handler_calls.append(router.name)

    alpha_module = ModuleType("sophie_bot.modules.alpha")
    alpha_module.router = alpha_router
    alpha_module.api_router = alpha_api_router
    alpha_module.__handlers__ = (FakeHandler,)

    async def alpha_pre_setup() -> None:
        pre_setup_calls.append("alpha")

    async def alpha_post_setup(modules: dict[str, ModuleType]) -> None:
        post_setup_calls.append(modules)

    alpha_module.__pre_setup__ = alpha_pre_setup
    alpha_module.__post_setup__ = alpha_post_setup

    beta_module = ModuleType("sophie_bot.modules.beta")
    beta_module.router = beta_router
    beta_module.__handlers__ = ()

    fake_modules: dict[str, ModuleType] = {
        "sophie_bot.modules.alpha": alpha_module,
        "sophie_bot.modules.beta": beta_module,
    }

    monkeypatch.setattr(module_loader, "MODULES", ["alpha", "beta"])
    monkeypatch.setattr(module_loader, "import_module", lambda path: fake_modules[path])

    runtime_registry = LoadedModuleRegistry()
    dispatcher = Dispatcher()
    result = await module_loader.load_modules(dispatcher, ["*"], register_handlers=True, registry=runtime_registry)

    assert result is runtime_registry
    assert runtime_registry.modules == {"alpha": alpha_module, "beta": beta_module}
    assert runtime_registry.api_routers == [alpha_api_router]
    assert list(module_loader.LOADED_MODULES.keys()) == ["alpha", "beta"]
    assert list(module_loader.LOADED_API_ROUTERS) == [alpha_api_router]
    assert handler_calls == ["alpha"]
    assert pre_setup_calls == ["alpha"]
    assert post_setup_calls == [runtime_registry.modules]


@pytest.mark.asyncio
async def test_load_modules_can_skip_handler_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    handler_calls: list[str] = []

    class FakeHandler:
        @classmethod
        def register(cls, router: Router) -> None:
            handler_calls.append(router.name)

    sample_router = Router(name="sample")
    sample_module = ModuleType("sophie_bot.modules.sample")
    sample_module.router = sample_router
    sample_module.__handlers__ = (FakeHandler,)

    monkeypatch.setattr(module_loader, "MODULES", ["sample"])
    monkeypatch.setattr(module_loader, "import_module", lambda path: sample_module)

    runtime_registry = LoadedModuleRegistry()
    await module_loader.load_modules(Dispatcher(), ["*"], register_handlers=False, registry=runtime_registry)

    assert runtime_registry.modules == {"sample": sample_module}
    assert handler_calls == []


def test_init_api_routers_includes_each_router() -> None:
    app = FastAPI()
    first_router = APIRouter()
    second_router = APIRouter()

    @first_router.get("/alpha")
    async def alpha() -> dict[str, str]:
        return {"status": "alpha"}

    @second_router.get("/beta")
    async def beta() -> dict[str, str]:
        return {"status": "beta"}

    init_api_routers(app, [first_router, second_router])

    route_paths = {route.path for route in app.routes}
    assert "/alpha" in route_paths
    assert "/beta" in route_paths