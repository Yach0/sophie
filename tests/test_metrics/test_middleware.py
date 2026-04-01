from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message, Update, User, Chat

from sophie_bot.metrics.middleware import MetricsMiddleware


@pytest.fixture
def mock_config():
    """Mock configuration for tests"""
    config = MagicMock()
    config.metrics_enable = True
    config.metrics_sample_ratio = 1.0
    return config


@pytest.fixture
def middleware(mock_config):
    """Create middleware instance for tests"""
    return MetricsMiddleware(mock_config)


@pytest.fixture
def mock_message():
    """Mock message for tests"""
    from datetime import datetime

    user = User(id=123, is_bot=False, first_name="Test")
    chat = Chat(id=456, type="private")
    return Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, text="Hello world")


@pytest.fixture
def mock_update(mock_message):
    """Mock update for tests"""
    return Update(update_id=1, message=mock_message)


class TestMetricsMiddleware:
    """Test suite for MetricsMiddleware"""

    @pytest.mark.asyncio
    async def test_successful_handler_execution(self, middleware: MetricsMiddleware, mock_update: Update):
        """Test successful handler execution metrics"""
        handler = AsyncMock(return_value="success")
        data = {}

        with (
            patch("sophie_bot.metrics.middleware.count_metric") as count_metric_mock,
            patch("sophie_bot.metrics.middleware.change_gauge_metric") as change_gauge_metric_mock,
            patch("sophie_bot.metrics.middleware.distribution_metric") as distribution_metric_mock,
        ):
            result = await middleware(handler, mock_update, data)

        # Check that handler was called
        handler.assert_called_once_with(mock_update, data)
        assert result == "success"

        assert count_metric_mock.call_count == 2
        assert change_gauge_metric_mock.call_count == 2
        distribution_metric_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_exception(self, middleware: MetricsMiddleware, mock_update: Update):
        """Test handler exception handling and error metrics"""
        test_exception = ValueError("Test error")
        handler = AsyncMock(side_effect=test_exception)
        data = {}

        with (
            patch("sophie_bot.metrics.middleware.count_metric") as count_metric_mock,
            patch("sophie_bot.metrics.middleware.change_gauge_metric") as change_gauge_metric_mock,
            patch("sophie_bot.metrics.middleware.distribution_metric") as distribution_metric_mock,
            pytest.raises(ValueError, match="Test error"),
        ):
            await middleware(handler, mock_update, data)

        assert count_metric_mock.call_count == 3
        assert change_gauge_metric_mock.call_count == 2
        distribution_metric_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_sampling_skip(self, mock_config: MagicMock, mock_update: Update):
        """Test that sampling works correctly"""
        mock_config.metrics_sample_ratio = 0.0  # Always skip
        middleware = MetricsMiddleware(mock_config)

        handler = AsyncMock(return_value="success")
        data = {}

        with (
            patch("random.random", return_value=0.5),
            patch("sophie_bot.metrics.middleware.count_metric") as count_metric_mock,
            patch("sophie_bot.metrics.middleware.change_gauge_metric") as change_gauge_metric_mock,
            patch("sophie_bot.metrics.middleware.distribution_metric") as distribution_metric_mock,
        ):
            result = await middleware(handler, mock_update, data)

        # Handler should still be called
        handler.assert_called_once_with(mock_update, data)
        assert result == "success"

        count_metric_mock.assert_not_called()
        change_gauge_metric_mock.assert_not_called()
        distribution_metric_mock.assert_not_called()

    def test_extract_update_info_message(self, middleware: MetricsMiddleware, mock_update: Update):
        """Test update info extraction for messages"""
        info = middleware._extract_update_info(mock_update, {})

        assert info["update_type"] == "message"
        assert info["chat_type"] == "private"
        assert info["transport"] == "polling"
        assert info["message_kind"] == "text"

    def test_extract_update_info_callback_query(self, middleware: MetricsMiddleware, mock_message: Message):
        """Test update info extraction for callback queries"""
        from aiogram.types import CallbackQuery

        callback_query = CallbackQuery(
            id="test",
            from_user=User(id=123, is_bot=False, first_name="Test"),
            chat_instance="test",
            message=mock_message,
            data="test_data",
        )
        update = Update(update_id=1, callback_query=callback_query)

        info = middleware._extract_update_info(update, {})

        assert info["update_type"] == "callback_query"
        assert info["chat_type"] == "private"
        assert info["message_kind"] is None

    def test_get_message_kind_text(self, middleware: MetricsMiddleware, mock_message: Message):
        """Test message kind extraction for text messages"""
        kind = middleware._get_message_kind(mock_message)
        assert kind == "text"

    def test_get_message_kind_photo(self, middleware: MetricsMiddleware):
        """Test message kind extraction for photo messages"""
        from aiogram.types import PhotoSize
        from datetime import datetime

        user = User(id=123, is_bot=False, first_name="Test")
        chat = Chat(id=456, type="private")
        photo = [PhotoSize(file_id="test", file_unique_id="test", width=100, height=100)]

        message = Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, photo=photo)

        kind = middleware._get_message_kind(message)
        assert kind == "photo"

    def test_extract_command_name_with_alt_prefix(self, middleware: MetricsMiddleware):
        """Test command extraction with alternate configured prefix."""
        from datetime import datetime

        user = User(id=123, is_bot=False, first_name="Test")
        chat = Chat(id=456, type="private")
        message = Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, text="!help test")

        command_name = middleware._extract_command_name(message)
        assert command_name == "help"

    def test_extract_command_name_with_mention(self, middleware: MetricsMiddleware):
        """Test command extraction when message contains bot mention."""
        from datetime import datetime

        user = User(id=123, is_bot=False, first_name="Test")
        chat = Chat(id=456, type="private")
        message = Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, text="/start@TestBot")

        command_name = middleware._extract_command_name(message)
        assert command_name == "start"

    def test_get_handler_name(self, middleware: MetricsMiddleware):
        """Test handler name extraction"""

        # Test with function name
        def test_handler():
            pass

        name = middleware._get_handler_name(test_handler, None)
        assert name == "test_handler"

        # Test with class method (should return class name, not method name)
        class TestHandler:
            def handle(self):
                pass

        handler_instance = TestHandler()
        name = middleware._get_handler_name(handler_instance.handle, None)
        assert name == "TestHandler"  # Expect class name, not method name

        # Test with functools.partial (common with aiogram class-based handlers)
        from functools import partial

        class AiPmStop:
            def handle(self):
                pass

        handler_instance = AiPmStop()
        partial_handler = partial(handler_instance.handle)
        name = middleware._get_handler_name(partial_handler, None)
        assert name == "AiPmStop"  # Should extract class name from partial

        # Test middleware object with memory address (the main issue we're fixing)
        class MockMiddleware:
            def __call__(self):
                pass  # Make it callable

            def __str__(self):
                return "<sophie_bot.middlewares.connections.ConnectionsMiddleware object at 0x7fa6c518c380>"

        mock_middleware = MockMiddleware()
        name = middleware._get_handler_name(mock_middleware, None)
        assert name == "ConnectionsMiddleware"  # Should extract clean class name

        # Test another middleware object format
        class MockMiddleware2:
            def __call__(self):
                pass

            def __str__(self):
                return "<MyCustomMiddleware object at 0x12345678>"

        mock_middleware2 = MockMiddleware2()
        name2 = middleware._get_handler_name(mock_middleware2, None)
        assert name2 == "MyCustomMiddleware"

        # Test name length limit
        long_name = "a" * 60
        setattr(test_handler, "__name__", long_name)
        name = middleware._get_handler_name(test_handler, None)
        assert len(name) == 50

    def test_webhook_transport_detection(self, middleware: MetricsMiddleware, mock_update: Update):
        """Test webhook transport detection"""
        data = {"webhook_info": True}
        info = middleware._extract_update_info(mock_update, data)
        assert info["transport"] == "webhook"

    @pytest.mark.asyncio
    async def test_concurrent_handlers(self, middleware: MetricsMiddleware, mock_update: Update):
        """Test that concurrent handlers are tracked correctly"""
        handler1 = AsyncMock()
        handler2 = AsyncMock()

        # Simulate slow handlers
        async def slow_handler1(*args, **kwargs):
            await asyncio.sleep(0.1)
            return "result1"

        async def slow_handler2(*args, **kwargs):
            await asyncio.sleep(0.1)
            return "result2"

        handler1.side_effect = slow_handler1
        handler2.side_effect = slow_handler2

        with patch("sophie_bot.metrics.middleware.change_gauge_metric") as change_gauge_metric_mock:
            # Run handlers concurrently
            results = await asyncio.gather(middleware(handler1, mock_update, {}), middleware(handler2, mock_update, {}))

        assert results == ["result1", "result2"]
        assert change_gauge_metric_mock.call_count == 4
