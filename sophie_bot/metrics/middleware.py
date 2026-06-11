from __future__ import annotations

import random
import time
from typing import Any, Awaitable, Callable, ClassVar, Dict, Optional, cast

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    InlineQuery,
    Message,
    TelegramObject,
    Update,
)

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import change_gauge_metric, count_metric, distribution_metric
from sophie_bot.utils.logger import log


class MetricsMiddleware(BaseMiddleware):
    """Aiogram middleware for collecting Sentry metrics."""

    def __init__(self, config: Any) -> None:
        self.config = config
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process update and collect metrics"""

        # Skip sampling if configured
        if self.config.metrics_sample_ratio < 1.0 and random.random() > self.config.metrics_sample_ratio:
            return await handler(event, data)

        # Extract update information
        update_info = self._extract_update_info(event, data)
        command_name = self._extract_command_name(event)

        # Increment update counter
        count_metric(
            "sophie.updates",
            attributes={
                "update_type": update_info["update_type"],
                "chat_type": update_info["chat_type"],
                "transport": update_info["transport"],
            },
        )

        # Increment message counter if it's a message
        if update_info["message_kind"]:
            count_metric("sophie.messages", attributes={"message_kind": update_info["message_kind"]})

        # Track inflight handlers
        change_gauge_metric("sophie.inflight_handlers", 1)

        # Measure handler duration
        start_time = time.perf_counter()
        handler_name = self._get_handler_name(handler, event)
        command_status = "ok"

        try:
            result = await handler(event, data)
            return result

        except Exception as e:
            command_status = "error"
            # Track handler errors
            exception_name = type(e).__name__
            count_metric(
                "sophie.handler_errors",
                attributes={"handler": handler_name, "exception": exception_name},
            )

            log.debug("Handler error tracked", handler=handler_name, exception_type=exception_name, error=str(e))

            # Re-raise the exception
            raise

        finally:
            # Always decrement inflight handlers and record duration
            change_gauge_metric("sophie.inflight_handlers", -1)

            # Record handler duration
            duration = time.perf_counter() - start_time
            distribution_metric(
                "sophie.handler.duration",
                duration,
                attributes={"handler": handler_name},
                unit="second",
            )

            if command_name is not None:
                count_metric(
                    "sophie.commands.executed",
                    attributes={
                        "command": command_name,
                        "chat_type": update_info["chat_type"],
                        "status": command_status,
                    },
                )
                distribution_metric(
                    "sophie.commands.duration",
                    duration,
                    attributes={"command": command_name, "status": command_status},
                    unit="second",
                )

    def _extract_update_info(self, event: TelegramObject, data: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """Extract update information for labeling"""

        update_type = "unknown"
        chat_type = "unknown"
        transport = "polling"  # Default, can be overridden
        message_kind = None

        # Determine transport method
        if hasattr(data, "webhook_info") or data.get("webhook_info"):
            transport = "webhook"

        # Extract update type and chat type
        if isinstance(event, Update):
            # Determine update type
            if event.message:
                update_type = "message"
                message_kind = self._get_message_kind(event.message)
                chat_type = event.message.chat.type if event.message.chat else "unknown"
            elif event.edited_message:
                update_type = "edited_message"
                message_kind = self._get_message_kind(event.edited_message)
                chat_type = event.edited_message.chat.type if event.edited_message.chat else "unknown"
            elif event.callback_query:
                update_type = "callback_query"
                chat_type = (
                    event.callback_query.message.chat.type
                    if (event.callback_query.message and event.callback_query.message.chat)
                    else "unknown"
                )
            elif event.inline_query:
                update_type = "inline_query"
                chat_type = "inline"
            elif event.chat_member:
                update_type = "chat_member"
                chat_type = event.chat_member.chat.type if event.chat_member.chat else "unknown"
            elif event.my_chat_member:
                update_type = "my_chat_member"
                chat_type = event.my_chat_member.chat.type if event.my_chat_member.chat else "unknown"
            elif event.chat_join_request:
                update_type = "chat_join_request"
                chat_type = event.chat_join_request.chat.type if event.chat_join_request.chat else "unknown"
        elif isinstance(event, Message):
            update_type = "message"
            message_kind = self._get_message_kind(event)
            chat_type = event.chat.type if event.chat else "unknown"
        elif isinstance(event, CallbackQuery):
            update_type = "callback_query"
            chat_type = event.message.chat.type if (event.message and event.message.chat) else "unknown"
        elif isinstance(event, InlineQuery):
            update_type = "inline_query"
            chat_type = "inline"
        elif isinstance(event, ChatMemberUpdated):
            update_type = "chat_member"
            chat_type = event.chat.type if event.chat else "unknown"
        elif isinstance(event, ChatJoinRequest):
            update_type = "chat_join_request"
            chat_type = event.chat.type if event.chat else "unknown"

        return {
            "update_type": update_type,
            "chat_type": chat_type,
            "transport": transport,
            "message_kind": message_kind,
        }

    # (message attribute, kind label) — checked in order, first truthy match wins
    _MESSAGE_KIND_MAP: ClassVar[list[tuple[str, str]]] = [
        ("text", "text"),
        ("photo", "photo"),
        ("video", "video"),
        ("audio", "audio"),
        ("voice", "voice"),
        ("document", "document"),
        ("sticker", "sticker"),
        ("animation", "animation"),
        ("video_note", "video_note"),
        ("contact", "contact"),
        ("location", "location"),
        ("venue", "venue"),
        ("poll", "poll"),
        ("dice", "dice"),
        ("game", "game"),
        ("invoice", "invoice"),
        ("successful_payment", "successful_payment"),
        ("connected_website", "connected_website"),
        ("passport_data", "passport_data"),
        ("proximity_alert_triggered", "proximity_alert"),
        ("forum_topic_created", "forum_topic_created"),
        ("forum_topic_closed", "forum_topic_closed"),
        ("forum_topic_reopened", "forum_topic_reopened"),
        ("general_forum_topic_hidden", "general_forum_topic_hidden"),
        ("general_forum_topic_unhidden", "general_forum_topic_unhidden"),
        ("write_access_allowed", "write_access_allowed"),
        ("user_shared", "user_shared"),
        ("chat_shared", "chat_shared"),
        ("new_chat_members", "new_chat_members"),
        ("left_chat_member", "left_chat_member"),
        ("new_chat_title", "new_chat_title"),
        ("new_chat_photo", "new_chat_photo"),
        ("delete_chat_photo", "delete_chat_photo"),
        ("group_chat_created", "group_chat_created"),
        ("supergroup_chat_created", "supergroup_chat_created"),
        ("channel_chat_created", "channel_chat_created"),
        ("migrate_to_chat_id", "migrate_to_chat_id"),
        ("migrate_from_chat_id", "migrate_from_chat_id"),
        ("pinned_message", "pinned_message"),
    ]

    def _get_message_kind(self, message: Message) -> str:
        """Determine message kind for labeling"""
        for attr, kind in self._MESSAGE_KIND_MAP:
            if getattr(message, attr, None):
                return kind
        return "other"

    def _extract_command_name(self, event: TelegramObject) -> str | None:
        message: Message | None = None

        if isinstance(event, Update):
            message = event.message or event.edited_message
        elif isinstance(event, Message):
            message = event

        if message is None or not message.text:
            return None

        text = message.text.strip()
        command_prefix = self._get_command_prefix(text)
        if command_prefix is None:
            return None

        first_token = text.split(" ", maxsplit=1)[0]
        command_without_mention = first_token.split("@", maxsplit=1)[0]
        command_name = command_without_mention.removeprefix(command_prefix).strip().lower()

        if not command_name:
            return None

        return command_name[:50]

    def _get_command_prefix(self, text: str) -> str | None:
        prefixes: list[str] = [str(prefix) for prefix in CONFIG.commands_prefix]
        if not prefixes:
            return None

        ordered_prefixes = sorted(prefixes, key=len, reverse=True)
        for prefix_value in ordered_prefixes:
            prefix = str(prefix_value)
            if text.startswith(prefix):
                return prefix

        return None

    def _get_handler_name(self, handler: Callable, event: TelegramObject | None) -> str:
        """Extract handler name for labeling"""
        handler_name: str = "unknown"
        _ = event

        # Handle functools.partial objects (common with aiogram class-based handlers)
        if hasattr(handler, "func"):
            # This is likely a functools.partial object
            actual_func = handler.func
            if hasattr(actual_func, "__self__") and hasattr(actual_func, "__class__"):
                # This is a bound method, get the class name
                handler_name = actual_func.__self__.__class__.__name__
            elif hasattr(actual_func, "__name__"):
                handler_name = cast(str, actual_func.__name__)
            else:
                handler_name = str(actual_func)
        # Handle bound methods directly
        elif hasattr(handler, "__self__"):
            self_obj = getattr(handler, "__self__", None)
            if self_obj is not None and hasattr(self_obj, "__class__"):
                handler_name = self_obj.__class__.__name__
        # Handle regular functions
        elif hasattr(handler, "__name__"):
            handler_name = cast(str, handler.__name__)
        # Check for problematic string representations first (before class check)
        elif callable(handler):
            handler_str = str(handler)
            # Try to extract class name from string representation
            if "bound method" in handler_str and "of <" in handler_str:
                # Example: "<bound method AiPmStop.handle of <...>>"
                parts = handler_str.split(".")
                if len(parts) >= 2:
                    class_part = parts[-2].split()[-1]  # Get the class name
                    handler_name = class_part
            elif " object at " in handler_str:
                # Handle cases like "<sophie_bot.middlewares.connections.ConnectionsMiddleware object at 0x...>"
                before_object = handler_str.split(" object at ")[0]
                handler_name = before_object.split(".")[-1] if "." in before_object else before_object.strip("<>")
            elif " " in handler_str:
                handler_name = handler_str.split(" ")[1]
            # Handle classes (if handler is a class itself) - moved after string parsing
            elif hasattr(handler, "__class__") and handler.__class__.__name__ != "function":
                handler_name = handler.__class__.__name__
            else:
                handler_name = handler_str

        # Clean up handler name to avoid high cardinality
        if "." in handler_name:
            handler_name = handler_name.split(".")[-1]
        if "<" in handler_name:
            handler_name = handler_name.replace("<", "").replace(">", "")
        if "'" in handler_name:
            handler_name = handler_name.replace("'", "")

        return handler_name[:50]  # Limit length to avoid cardinality issues
