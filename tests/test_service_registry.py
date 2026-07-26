from inspect import isawaitable

import pytest

from sophie_bot.services.registry import ServiceRegistry


class FakeServiceRegistry(ServiceRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.created_services: list[str] = []

    def _create_redis(self) -> str:
        self.created_services.append("redis")
        return "redis-instance"

    def _create_mongo(self) -> str:
        self.created_services.append("mongo")
        return "mongo-instance"

    def _create_bot(self) -> str:
        self.created_services.append("bot")
        return "bot-instance"

    def _create_dispatcher(self) -> str:
        self.created_services.append("dispatcher")
        return "dispatcher-instance"

    def _create_scheduler(self) -> str:
        self.created_services.append("scheduler")
        return "scheduler-instance"


def test_service_registry_creates_services_lazily_and_caches_instances() -> None:
    registry = FakeServiceRegistry()

    assert registry.get_redis() == "redis-instance"
    assert registry.get_redis() == "redis-instance"
    assert registry.get_mongo() == "mongo-instance"
    assert registry.get_bot() == "bot-instance"
    assert registry.get_dispatcher() == "dispatcher-instance"
    assert registry.get_scheduler() == "scheduler-instance"

    assert registry.created_services == ["redis", "mongo", "bot", "dispatcher", "scheduler"]


def test_service_registry_override_replaces_cached_instance() -> None:
    registry = FakeServiceRegistry()

    assert registry.get_redis() == "redis-instance"
    registry.override("redis", "override-redis")

    assert registry.get_redis() == "override-redis"
    assert registry.created_services == ["redis"]


def test_service_registry_reset_clears_overrides_and_cached_instances() -> None:
    registry = FakeServiceRegistry()
    registry.override("bot", "override-bot")

    assert registry.get_bot() == "override-bot"

    registry.reset()

    assert registry.get_bot() == "bot-instance"
    assert registry.created_services == ["bot"]


def test_service_registry_is_testing_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESTING", raising=False)
    assert ServiceRegistry._is_testing() is False

    monkeypatch.setenv("TESTING", "1")
    assert ServiceRegistry._is_testing() is True


@pytest.mark.asyncio
async def test_service_registry_redis_factory_uses_fake_redis_when_testing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTING", "1")
    redis_client = ServiceRegistry()._create_redis()

    assert type(redis_client).__name__ == "FakeRedis"
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_service_registry_core_factories_create_clients_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TESTING", raising=False)
    registry = ServiceRegistry()

    redis_client = registry._create_redis()
    mongo_client = registry._create_mongo()
    bot = registry._create_bot()
    scheduler = None

    try:
        dispatcher = registry._create_dispatcher()
        scheduler = registry._create_scheduler()

        assert type(redis_client).__name__ == "Redis"
        assert type(mongo_client).__name__ in {"AsyncMongoClient", "AsyncMongoMockClient"}
        assert type(bot).__name__ == "Bot"
        assert type(dispatcher).__name__ == "Dispatcher"
        assert type(scheduler).__name__ == "AsyncIOScheduler"
    finally:
        await redis_client.aclose()
        close_result = mongo_client.close()
        if isawaitable(close_result):
            await close_result
        if scheduler is not None:
            scheduler._jobstores["default"].client.close()
        await bot.session.close()
