from __future__ import annotations

from asyncio import gather
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import BinaryIO, cast

from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message
from mistralai.client.models.assistantmessage import AssistantMessage
from mistralai.client.models.systemmessage import SystemMessage
from mistralai.client.models.usermessage import UserMessage
from normality import normalize
from openai.types.moderation_text_input_param import ModerationTextInputParam
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from stfu_tg import Doc, HList, KeyValue, Section, Template, VList
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.modules.ai.utils.cache_messages import (
    MessageType,
    get_cached_messages,
)
from sophie_bot.modules.ai.utils.chatbot_tool_history import ToolExchange
from sophie_bot.modules.ai.utils.self_reply import cut_titlebar, is_ai_message, message_text
from sophie_bot.modules.ai.utils.transform_audio import transform_voice_to_text
from sophie_bot.modules.ai.utils.transform_video import transform_video_to_text
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

CHATBOT_CACHE_MESSAGE_LIMIT = 35


class AIUserMessageFormatter:
    @staticmethod
    def sanitize_name(name: str) -> str:
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-()[] ")
        return "".join(char for char in name if char in allowed_chars) or "Unknown"

    @classmethod
    def user_message(
        cls,
        text: str,
        name: str,
        reply_to_user: str | None = None,
    ) -> str:
        name = cls.sanitize_name(name)
        if reply_to_user:
            reply_to_user = cls.sanitize_name(reply_to_user)
            name = f"{name} ({_('reply to')} {reply_to_user})"

        return f"{name}: {text}"


async def _admin_context_name(chat_tid: int, user_tid: int, name: str, is_group: bool) -> str:
    if not is_group or not await is_enabled("ai_chatbot_admin_status", chat_tid=chat_tid):
        return name

    chat_model = await ChatModel.get_by_tid(chat_tid)
    user_model = await ChatModel.get_by_tid(user_tid)
    if not chat_model or not user_model:
        return name
    if chat_model.type not in {ChatType.group, ChatType.supergroup}:
        return name

    admin = await ChatAdminModel.find_one(
        ChatAdminModel.chat.id == chat_model.iid,
        ChatAdminModel.user.id == user_model.iid,
    )
    if not admin:
        return name

    if admin.member.status == ChatMemberStatus.CREATOR:
        role = "Owner"
    elif admin.member.status == ChatMemberStatus.ADMINISTRATOR:
        role = "Admin"
    else:
        return name

    admin_member = cast(ChatMemberAdministrator | ChatMemberOwner, admin.member)
    custom_title = admin_member.custom_title
    if custom_title:
        return f"{name} [{role} - {custom_title}]"
    return f"{name} [{role}]"


def _extract_message_content(
    message: Message,
    custom_text: str | None,
    normalize_texts: bool,
    is_sophie: bool,
) -> str:
    """Extract text, caption, media info from the message. Returns the processed message text."""
    message_text = custom_text or message.text or message.caption or _("<No text provided>")
    if normalize_texts:
        message_text = normalize(message_text) or _("<No text provided>")

    # Cut the AI titlebar
    if is_sophie and is_ai_message(message_text):
        message_text = cut_titlebar(message_text)

    return message_text


async def _build_message_parts(
    message: Message,
    message_text: str,
    from_user_name: str,
    replied_user_name: str | None,
    disable_name: bool,
) -> list[UserContent]:
    """Build the list of message parts for the AI context."""
    prompt: list[UserContent] = []

    # Message's text
    prompt.append(
        message_text
        if disable_name
        else AIUserMessageFormatter.user_message(
            text=message_text,
            name=from_user_name,
            reply_to_user=replied_user_name,
        )
    )

    # Visual media
    if message.photo or message.sticker or message.animation:
        # Determine a file_id to download irrespective of underlying Telegram type
        if message.photo:
            image_file_id = message.photo[-1].file_id
        elif (
            message.sticker and (message.sticker.is_animated or message.sticker.is_video) and message.sticker.thumbnail
        ):
            image_file_id = message.sticker.thumbnail.file_id
        elif message.animation and message.animation.thumbnail:
            image_file_id = message.animation.thumbnail.file_id
        elif message.sticker:
            image_file_id = message.sticker.file_id
        else:
            # Animation without thumbnail — cannot extract visual media, skip gracefully
            log.warning("Skipping visual media extraction: %s without thumbnail", message.animation)
            return prompt

        downloaded_image: BinaryIO | None = await bot.download(image_file_id)

        if not downloaded_image:
            raise SophieException(_("Image is empty"))

        prompt.append(
            BinaryContent(
                media_type="image/jpeg",
                data=downloaded_image.read(),
            )
        )

    # Voice
    if message.voice:
        voice_text = await transform_voice_to_text(message.voice)
        prompt.append(voice_text)
        # TODO: Cache message somehow again?

    # Video - extract thumbnail and transcribe audio
    if message.video or message.video_note:
        video = message.video or message.video_note

        # Add video thumbnail if available
        if video and video.thumbnail:
            thumbnail_file_id = video.thumbnail.file_id
            downloaded_thumbnail: BinaryIO | None = await bot.download(thumbnail_file_id)

            if downloaded_thumbnail:
                prompt.append(
                    BinaryContent(
                        media_type="image/jpeg",
                        data=downloaded_thumbnail.read(),
                    )
                )

        # Transcribe video audio
        if video:
            video_transcription = await transform_video_to_text(video)
            if video_transcription:
                prompt.append(str(Template(_("[Video transcription: {text}]"), text=video_transcription)))

    return prompt


class AIMessageHistory:
    """
    This class is used to store and construct the messages that are sent to the AI.
    """

    message_history: list[ModelRequest | ModelResponse]
    prompt: list[UserContent]
    context_lines: list[str]

    def __init__(self):
        self.message_history = []
        self.prompt = []
        self.context_lines = []

    @staticmethod
    def _is_ai_dialogue(msg: MessageType) -> bool:
        """Whether a cached user message was actually part of the AI conversation.

        Background group chatter (everything else) must not become a standalone user turn:
        trailing unanswered user turns get merged into the current prompt by the provider,
        which makes the model answer several messages at once.
        """
        return bool(
            msg.handled_by_ai
            or msg.reply_to_is_sophie_ai
            or msg.has_ai_command
            or msg.is_ai_filter_reply
            or msg.proactively_answered
        )

    async def _format_context_line(self, chat_id: int, msg: MessageType) -> str:
        user = await ChatModel.get_by_tid(msg.user_id)
        first_name = user.first_name_or_title if user else "Unknown"
        from_user_name = await _admin_context_name(chat_id, msg.user_id, first_name, is_group=True)
        return AIUserMessageFormatter.user_message(
            msg.text,
            from_user_name,
            reply_to_user=msg.reply_to_user_name,
        )

    def _fold_trailing_requests(self) -> None:
        """Move trailing unanswered user turns out of the history and into the context block."""
        folded: list[str] = []
        while self.message_history and isinstance(request := self.message_history[-1], ModelRequest):
            # A tool return must stay attached to the call that precedes it, or the provider sees a
            # tool call with no result.
            if any(isinstance(part, ToolReturnPart) for part in request.parts):
                break
            self.message_history.pop()
            folded.extend(
                part.content
                for part in request.parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            )
        self.context_lines.extend(reversed(folded))

    def apply_context_block(self) -> None:
        """Prepend collected background chatter to the prompt as reference-only context."""
        if not self.context_lines:
            return
        context_block = Doc(
            Section(
                VList(*self.context_lines),
                title=_("Recent chat messages (context only — respond solely to the latest message)"),
            )
        ).to_md()
        self.prompt = [context_block, *self.prompt]
        self.context_lines = []

    @staticmethod
    async def _cache_transform_msg(chat_id: int, msg: MessageType) -> ModelResponse | ModelRequest:
        """Transforms a message from the cache to a message that can be sent to the AI."""
        user = await ChatModel.get_by_tid(msg.user_id)
        first_name = user.first_name_or_title if user else "Unknown"

        if msg.user_id == CONFIG.bot_id:
            stored_message_text = message_text(msg)
            text = cut_titlebar(stored_message_text) if is_ai_message(stored_message_text) else stored_message_text
            return ModelResponse(parts=[TextPart(content=text)])

        from_user_name = await _admin_context_name(chat_id, msg.user_id, first_name, is_group=True)
        return ModelRequest(
            parts=[
                UserPromptPart(
                    content=AIUserMessageFormatter.user_message(
                        msg.text,
                        from_user_name,
                        reply_to_user=msg.reply_to_user_name,
                    )
                )
            ]
        )

    async def add_from_cache(
        self,
        chat_id: int,
        limit: int | None = None,
        fold_background: bool = False,
        max_age: timedelta | None = None,
        tool_exchanges: Mapping[int, Sequence[ToolExchange]] | None = None,
    ) -> None:
        """Adds messages from the cache to the message history.

        With ``fold_background`` enabled, only genuine AI-conversation messages (and Sophie's own
        replies) become conversation turns; unrelated group chatter is collected into
        ``context_lines`` instead, to be surfaced as reference-only context via
        :meth:`apply_context_block`. This prevents the model from treating a backlog of unanswered
        group messages as the current turn and answering all of them at once.

        ``tool_exchanges`` maps a Sophie message ID to the tool call/return pairs that produced it.
        They are replayed right before that answer, so the model can reuse what it already looked up
        instead of running the same searches again.
        """
        messages = await get_cached_messages(chat_id, limit=limit, max_age=max_age)
        exchanges = tool_exchanges or {}

        if not fold_background:
            for msg, transformed in zip(
                messages,
                await gather(*[self._cache_transform_msg(chat_id, msg) for msg in messages]),
                strict=True,
            ):
                self.message_history.extend(exchanges.get(msg.message_id, ()))
                self.message_history.append(transformed)
            return

        for msg in messages:
            if msg.user_id == CONFIG.bot_id or self._is_ai_dialogue(msg):
                self.message_history.extend(exchanges.get(msg.message_id, ()))
                self.message_history.append(await self._cache_transform_msg(chat_id, msg))
            else:
                self.context_lines.append(await self._format_context_line(chat_id, msg))

        self._fold_trailing_requests()

    async def add_from_message(
        self,
        message: Message,
        custom_text: str | None = None,
        normalize_texts: bool = False,
        allow_reply_messages: bool = True,
        disable_name: bool = False,
    ) -> None:
        """Adds a user message to the context, returns a list of additional messages to cache for future use."""

        # Handle replied message first
        replied_user_name: str | None = None
        if allow_reply_messages and message.reply_to_message and message.reply_to_message.from_user:
            replied_user_name = message.reply_to_message.from_user.full_name
            await self.add_from_message(message.reply_to_message, allow_reply_messages=False)

        if not message.from_user:  # Linter insists on checking this
            return

        is_sophie = message.from_user.id == CONFIG.bot_id

        message_text = _extract_message_content(message, custom_text, normalize_texts, is_sophie)

        prompt: list[UserContent] = self.prompt or []
        from_user_name = await _admin_context_name(
            message.chat.id,
            message.from_user.id,
            message.from_user.full_name,
            message.chat.type in {"group", "supergroup"},
        )
        prompt.extend(
            await _build_message_parts(message, message_text, from_user_name, replied_user_name, disable_name)
        )

        self.prompt = prompt

    def add_system(self, content: str) -> None:
        """Add a system message to the message history."""
        self.message_history.append(ModelRequest(parts=[SystemPromptPart(content=content)]))

    def add_custom(self, content: str, name: str | None) -> None:
        """Add a custom user message to the message history."""
        user_content = AIUserMessageFormatter.user_message(content, name or "User")
        self.message_history.append(ModelRequest(parts=[UserPromptPart(content=user_content)]))

    def history_debug(self) -> Element:
        """Builds a debug message for the message history."""
        items = VList(prefix="\n")

        for msg in self.message_history:
            kv_parts: list[Element] = []
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    kv_parts.append(KeyValue(part.tool_name, part.args))
                else:
                    # not all parts have 'content' attribute; guard for mypy and runtime
                    content_value = part.content if hasattr(part, "content") else str(part)
                    kv_parts.append(KeyValue(part.part_kind, content_value))
            items.append(HList(*kv_parts))

        items += Section(
            HList(*(item.kind if not isinstance(item, str) else item for item in self.prompt)),
            title="Prompt",
        )

        return items

    @property
    def to_moderation(
        self,
    ) -> list[dict[str, str]]:
        """Extract chat messages for moderation in {role, content} format."""
        moderation_content: list[dict[str, str]] = []

        # Extract content from message history
        for msg in self.message_history:
            if isinstance(msg, (ModelRequest, ModelResponse)):
                for part in msg.parts:
                    if isinstance(part, SystemPromptPart):
                        moderation_content.append({"role": "system", "content": part.content})
                    elif isinstance(part, TextPart):
                        # TextPart is from assistant responses
                        moderation_content.append({"role": "assistant", "content": part.content})
                    elif isinstance(part, UserPromptPart):
                        content_str = part.content if isinstance(part.content, str) else str(part.content)
                        moderation_content.append({"role": "user", "content": content_str})
                    elif isinstance(part, BinaryContent):
                        # Binary content (images/audio) is skipped for moderation input here
                        pass

        # Extract content from current prompt (treat as user content)
        if self.prompt:
            for content in self.prompt:
                if isinstance(content, str):
                    moderation_content.append({"role": "user", "content": content})

        return moderation_content


def convert_to_openai_moderation_format(messages: list[dict[str, str]]) -> list[ModerationTextInputParam]:
    """Convert plain dict messages to OpenAI moderation input parts.

    OpenAI's moderation endpoint takes content parts rather than chat messages, so roles are
    dropped: it has no notion of who said what.
    """
    return [
        ModerationTextInputParam(type="text", text=content) for msg in messages if (content := msg.get("content", ""))
    ]


def convert_to_moderation_format(
    messages: list[dict[str, str]],
) -> list[SystemMessage | UserMessage | AssistantMessage]:
    """Convert plain dict messages to Mistral SDK message objects for moderation."""
    result: list[SystemMessage | UserMessage | AssistantMessage] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(UserMessage(content=content))
        elif role == "assistant":
            result.append(AssistantMessage(content=content))
    return result
