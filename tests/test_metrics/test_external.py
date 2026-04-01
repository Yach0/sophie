from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from sophie_bot.metrics.external import (
    ExternalServiceTracker,
    create_service_tracker,
    instrument_external_service,
    instrument_mongo,
    instrument_openai,
    instrument_redis,
    time_external_service,
)


@pytest.fixture
def mock_config() -> MagicMock:
    config = MagicMock()
    config.metrics_enable = True
    return config


@pytest.fixture(autouse=True)
def setup_config(mock_config: MagicMock):
    with patch("sophie_bot.metrics.external.CONFIG", mock_config):
        yield


class TestExternalServiceInstrumentation:
    @pytest.mark.asyncio
    async def test_time_external_service_success(self) -> None:
        with (
            patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock,
            patch("sophie_bot.metrics.external.count_metric") as count_metric_mock,
        ):
            async with time_external_service("test_service"):
                await asyncio.sleep(0.01)

        distribution_metric_mock.assert_called_once()
        count_metric_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_time_external_service_exception(self) -> None:
        with (
            patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock,
            patch("sophie_bot.metrics.external.count_metric") as count_metric_mock,
            pytest.raises(ValueError, match="Test error"),
        ):
            async with time_external_service("test_service"):
                raise ValueError("Test error")

        distribution_metric_mock.assert_called_once()
        count_metric_mock.assert_called_once_with(
            "sophie.external_errors",
            attributes={"service": "test_service", "exception": "ValueError"},
        )

    @pytest.mark.asyncio
    async def test_time_external_service_disabled(self, mock_config: MagicMock) -> None:
        mock_config.metrics_enable = False

        with (
            patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock,
            patch("sophie_bot.metrics.external.count_metric") as count_metric_mock,
        ):
            async with time_external_service("test_service"):
                await asyncio.sleep(0.01)

        distribution_metric_mock.assert_not_called()
        count_metric_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_instrument_external_service_async(self) -> None:
        @instrument_external_service("test_service")
        async def test_async_function() -> str:
            await asyncio.sleep(0.01)
            return "success"

        with patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock:
            result = await test_async_function()

        assert result == "success"
        distribution_metric_mock.assert_called_once()

    def test_instrument_external_service_sync(self) -> None:
        @instrument_external_service("test_service")
        def test_sync_function() -> str:
            return "success"

        with patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock:
            result = test_sync_function()

        assert result == "success"
        distribution_metric_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_instrument_external_service_async_exception(self) -> None:
        @instrument_external_service("test_service")
        async def test_async_function() -> None:
            raise ValueError("Test error")

        with (
            patch("sophie_bot.metrics.external.count_metric") as count_metric_mock,
            pytest.raises(ValueError, match="Test error"),
        ):
            await test_async_function()

        count_metric_mock.assert_called_once_with(
            "sophie.external_errors",
            attributes={"service": "test_service", "exception": "ValueError"},
        )

    def test_instrument_external_service_sync_exception(self) -> None:
        @instrument_external_service("test_service")
        def test_sync_function() -> None:
            raise ValueError("Test error")

        with (
            patch("sophie_bot.metrics.external.count_metric") as count_metric_mock,
            pytest.raises(ValueError, match="Test error"),
        ):
            test_sync_function()

        count_metric_mock.assert_called_once_with(
            "sophie.external_errors",
            attributes={"service": "test_service", "exception": "ValueError"},
        )

    @pytest.mark.asyncio
    async def test_specific_service_decorators(self) -> None:
        @instrument_mongo("find")
        async def mongo_operation() -> str:
            return "mongo_result"

        @instrument_redis("get")
        async def redis_operation() -> str:
            return "redis_result"

        @instrument_openai("completion")
        async def openai_operation() -> str:
            return "openai_result"

        with patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock:
            assert await mongo_operation() == "mongo_result"
            assert await redis_operation() == "redis_result"
            assert await openai_operation() == "openai_result"

        assert distribution_metric_mock.call_count == 5


class TestExternalServiceTracker:
    def test_tracker_success(self) -> None:
        tracker = create_service_tracker("manual_service")

        with (
            patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock,
            patch("sophie_bot.metrics.external.count_metric") as count_metric_mock,
        ):
            tracker.start()
            tracker.finish()

        distribution_metric_mock.assert_called_once()
        count_metric_mock.assert_not_called()

    def test_tracker_with_exception(self) -> None:
        tracker = create_service_tracker("manual_service")

        with (
            patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock,
            patch("sophie_bot.metrics.external.count_metric") as count_metric_mock,
        ):
            tracker.start()
            tracker.finish(ValueError("Test error"))

        distribution_metric_mock.assert_called_once()
        count_metric_mock.assert_called_once_with(
            "sophie.external_errors",
            attributes={"service": "manual_service", "exception": "ValueError"},
        )

    def test_tracker_without_start(self) -> None:
        tracker = create_service_tracker("manual_service")
        with patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock:
            tracker.finish()
        distribution_metric_mock.assert_not_called()

    def test_tracker_disabled_metrics(self, mock_config: MagicMock) -> None:
        mock_config.metrics_enable = False
        tracker = create_service_tracker("manual_service")
        tracker.start()
        with patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock:
            tracker.finish()
        distribution_metric_mock.assert_not_called()


class TestServiceIntegration:
    @pytest.mark.asyncio
    async def test_service_context_managers(self) -> None:
        from sophie_bot.metrics.external import (
            time_mongo_operation,
            time_openai_operation,
            time_redis_operation,
            time_telegram_api_operation,
        )

        with patch("sophie_bot.metrics.external.distribution_metric") as distribution_metric_mock:
            async with time_mongo_operation("find"):
                pass
            async with time_redis_operation("set"):
                pass
            async with time_openai_operation("chat"):
                pass
            async with time_telegram_api_operation("send_message"):
                pass

        assert distribution_metric_mock.call_count == 7

    def test_create_service_tracker_type(self) -> None:
        tracker = create_service_tracker("test")
        assert isinstance(tracker, ExternalServiceTracker)
        assert tracker.service_name == "test"
