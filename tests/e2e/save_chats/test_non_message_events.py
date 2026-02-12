"""Tests for non-message events handling in SaveChatsMiddleware.

This module tests how the middleware handles callback queries, inline queries,
poll answers, and other non-message update types.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.db.models.chat import ChatModel


class TestNonMessageEvents:
    """Test handling of non-message events."""

    @pytest.mark.asyncio
    async def test_callback_query_saves_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that callback query saves the user."""
        from aiogram.types import CallbackQuery, Chat, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", username="testuser", is_bot=False)
        chat = Chat(id=123456789, type="private", first_name="Test")

        callback_query = CallbackQuery(
            id="test_id",
            from_user=user,
            chat_instance="test_instance",
        )

        update = Update(update_id=1, callback_query=callback_query)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None
        assert db_user.first_name_or_title == "Test"

    @pytest.mark.asyncio
    async def test_inline_query_saves_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that inline query saves the user."""
        from aiogram.types import InlineQuery, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", username="testuser", is_bot=False)

        inline_query = InlineQuery(
            id="test_query",
            from_user=user,
            query="test",
            offset="",
        )

        update = Update(update_id=1, inline_query=inline_query)
        base_data["event_from_user"] = user

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None

    @pytest.mark.asyncio
    async def test_poll_answer_saves_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that poll answer saves the user."""
        from aiogram.types import PollAnswer, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", username="testuser", is_bot=False)

        poll_answer = PollAnswer(
            poll_id="test_poll",
            user=user,
            option_ids=[0],
        )

        update = Update(update_id=1, poll_answer=poll_answer)
        base_data["event_from_user"] = user

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None

    @pytest.mark.asyncio
    async def test_non_update_event_passes_through(
        self,
        middleware,
        mock_handler,
    ) -> None:
        """Test that non-Update events pass through without processing."""
        # Arrange
        event = {"some": "data"}
        data: dict = {}

        # Act
        result = await middleware(mock_handler, event, data)

        # Assert
        assert result == "handler_result"
        mock_handler.assert_called_once_with(event, data)

    @pytest.mark.asyncio
    async def test_update_with_no_event_type(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test update with no recognized event type."""
        from aiogram.types import Update

        # Arrange
        update = Update(update_id=1)  # Empty update

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_edited_message_not_processed(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that edited messages are not processed by this middleware."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        edited_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            text="Edited text",
        )

        update = Update(update_id=1, edited_message=edited_message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Should pass through without processing
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_channel_post_not_processed(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that channel posts are not processed by this middleware."""
        from aiogram.types import Chat, Message, Update

        # Arrange
        chat = Chat(id=-1001234567890, type="channel", title="Test Channel")

        channel_post = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            # No from_user for channel posts
        )

        update = Update(update_id=1, channel_post=channel_post)
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Should pass through without processing
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_pre_checkout_query(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test pre-checkout query handling."""
        from aiogram.types import PreCheckoutQuery, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)

        pre_checkout_query = PreCheckoutQuery(
            id="test_query",
            from_user=user,
            currency="USD",
            total_amount=100,
            invoice_payload="test_payload",
        )

        update = Update(update_id=1, pre_checkout_query=pre_checkout_query)
        base_data["event_from_user"] = user

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Should pass through (not handled by this middleware)
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_shipping_query(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test shipping query handling."""
        from aiogram.types import ShippingAddress, ShippingQuery, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)

        # ShippingQuery requires a valid ShippingAddress object
        shipping_address = ShippingAddress(
            country_code="US",
            state="CA",
            city="San Francisco",
            street_line1="123 Main St",
            street_line2="",
            post_code="94102",
        )

        shipping_query = ShippingQuery(
            id="test_query",
            from_user=user,
            invoice_payload="test_payload",
            shipping_address=shipping_address,
        )

        update = Update(update_id=1, shipping_query=shipping_query)
        base_data["event_from_user"] = user

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Should pass through (not handled by this middleware)
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_chosen_inline_result(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test chosen inline result handling."""
        from aiogram.types import ChosenInlineResult, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)

        chosen_result = ChosenInlineResult(
            result_id="test_result",
            from_user=user,
            query="test",
        )

        update = Update(update_id=1, chosen_inline_result=chosen_result)
        base_data["event_from_user"] = user

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Should pass through (not handled by this middleware)
        assert result == "handler_result"
