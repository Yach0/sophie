from __future__ import annotations

from abc import abstractmethod
from datetime import UTC, datetime
from re import fullmatch
from secrets import token_urlsafe
from typing import ClassVar, Literal

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from ass_tg.types import OptionalArg, OrArg, TextArg, UserMentionArg, WordArg
from redis.asyncio import Redis
from stfu_tg import Doc, Template

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryModel
from sophie_bot.db.models.ai.ai_evidence_memory import MemoryAction, MemoryActions, MemorySource, MemoryState
from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.callbacks import AIMemoryClearCallback, AIMemoryPageCallback, MemoryListView
from sophie_bot.modules.ai.utils.ai_mode import resolve_chat_capabilities
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message
from sophie_bot.modules.ai.utils.cache_messages import reset_modern_context
from sophie_bot.modules.ai.utils.chatbot_tool_history import reset_tool_exchanges
from sophie_bot.modules.ai.utils.evidence_memory import (
    apply_actions,
    capture_source,
    clear_memory,
    memory_cutoff,
    mutate_memory,
    read_memory,
)
from sophie_bot.modules.ai.utils.memory_commands import MemoryCommand
from sophie_bot.modules.ai.utils.memory_view import (
    MemoryBrowser,
    MemoryMember,
    can_correct_memory,
    memory_back_markup,
    memory_help,
    memory_view,
)
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.reply_or_edit import reply_or_edit
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

_BROWSE_TTL_SECONDS = 15 * 60
_CLEAR_TTL_SECONDS = 5 * 60


def _browser_key(chat_tid: int, message_chat_tid: int, token: str) -> str:
    return f"ai:memory:browse:{chat_tid}:{message_chat_tid}:{token}"


def _clear_key(chat_tid: int, message_chat_tid: int, data: AIMemoryClearCallback) -> str:
    return f"ai:memory:clear:{chat_tid}:{message_chat_tid}:{data.owner_tid}:{data.operation}:{data.token}"


_MEMORY_HELP_GROUP = l_("Memory")
_LEGACY_MEMORY_COMMANDS = {
    "show": MemoryCommand.browse,
    "sources": MemoryCommand.sources,
    "forget": MemoryCommand.forget,
    "off": MemoryCommand.off,
    "on": MemoryCommand.on,
}


class _AIMemoryCommand(SophieMessageHandler):
    command: ClassVar[MemoryCommand]

    @classmethod
    def filters(cls) -> tuple[CallbackType, ...]:
        return CMDFilter(cls.command.value), FeatureFlagFilter("ai_memory_controls")

    async def handle(self) -> Message:
        sender = self.event.from_user
        if sender is None or self.event.sender_chat:
            return await self.event.reply(_("Send this command as yourself to manage your memory."))
        return await self.run(sender)

    @abstractmethod
    async def run(self, sender: User) -> Message:
        pass


class _AIMemoryBrowse(_AIMemoryCommand):
    view: ClassVar[MemoryListView]

    async def run(self, sender: User) -> Message:
        return await self.open_browser(MemoryBrowser(owner_tid=sender.id))

    async def open_browser(self, browser: MemoryBrowser) -> Message:
        chat = self.connection.db_model
        redis = self.services.redis
        data = AIMemoryPageCallback(view=self.view, token=token_urlsafe(8))
        await redis.set(
            _browser_key(chat.tid, self.event.chat.id, data.token), browser.model_dump_json(), ex=_BROWSE_TTL_SECONDS
        )
        document, markup = await _memory_page(chat, browser, data, redis=redis)
        return await send_ai_rich_message(self.event, document, reply_markup=markup)


@flags.handler_help(
    group=_MEMORY_HELP_GROUP,
    description=l_("Browse your memories, search by text, or view a member's memories by mention or reply."),
)
@flags.args(query=OptionalArg(OrArg(UserMentionArg(), TextArg(), description=l_("?@username or search text"))))
class AIMemoryBrowse(_AIMemoryBrowse):
    command = MemoryCommand.browse
    view = "show"

    async def run(self, sender: User) -> Message:
        query: str | User | None = self.data.get("query")
        search = ""
        member = None
        if isinstance(query, User):
            member = MemoryMember(tid=query.id, name=query.full_name)
        elif query:
            search = " ".join(query.split())
            if replacement := _LEGACY_MEMORY_COMMANDS.get(search):
                return await self.event.reply(
                    Template(_("This command moved. Use {command}."), command=f"/{replacement}").to_html()
                )
            if fullmatch(r"correct(?: [0-9a-f]{32}(?: .*)?)?", search):
                return await self.event.reply(
                    Template(
                        _("Use {command} with a memory ID and replacement text."),
                        command=f"/{MemoryCommand.correct}",
                    ).to_html()
                )
            if fullmatch(r"@[A-Za-z0-9_]{1,32}", search):
                user = await ChatModel.find_one(
                    ChatModel.type == ChatType.private, {"username": {"$regex": f"^{search[1:]}$", "$options": "i"}}
                )
                if user is None:
                    return await self.event.reply(_("I could not find that username. Try replying to their message."))
                full_name = " ".join(filter(None, (user.first_name_or_title, user.last_name)))
                member = MemoryMember(tid=user.tid, name=full_name)
                search = ""
        elif (reply := self.event.reply_to_message) and is_real_reply(self.event):
            if reply.sender_chat or not reply.from_user:
                return await self.event.reply(_("Reply to a message sent by a user."))
            member = MemoryMember(tid=reply.from_user.id, name=reply.from_user.full_name)

        if member is not None and member.tid == sender.id:
            member = None
        return await self.open_browser(MemoryBrowser(owner_tid=sender.id, search=search, member=member))


@flags.handler_help(group=_MEMORY_HELP_GROUP, description=l_("Browse previous versions of your memories."))
class AIMemoryHistory(_AIMemoryBrowse):
    command = MemoryCommand.history
    view = "history"


@flags.handler_help(group=_MEMORY_HELP_GROUP, description=l_("Browse your original messages saved as sources."))
class AIMemorySources(_AIMemoryBrowse):
    command = MemoryCommand.sources
    view = "sources"


@flags.handler_help(group=_MEMORY_HELP_GROUP, description=l_("Show examples for the AI memory commands."))
class AIMemoryHelp(_AIMemoryCommand):
    command = MemoryCommand.help

    async def run(self, sender: User) -> Message:
        return await send_ai_rich_message(self.event, memory_help())


@flags.handler_help(group=_MEMORY_HELP_GROUP, description=l_("Correct one of your current personal memories."))
@flags.args(fact_id=WordArg(l_("Memory ID")), text=TextArg(l_("Replacement text")))
class AIMemoryCorrect(_AIMemoryCommand):
    command = MemoryCommand.correct

    async def run(self, sender: User) -> Message:
        chat = self.connection.db_model
        user_tid = sender.id
        if not (await resolve_chat_capabilities(chat)).memory:
            return await self.event.reply(_("Memory capture requires entertainment mode in this chat."))
        if self.event.chat.id != chat.tid:
            return await self.event.reply(_("Send the correction in the original chat and topic."))
        fact_id: str = self.data["fact_id"]
        text: str = self.data["text"].strip()
        if not text or len(text) > 600:
            return await self.event.reply(_("Enter replacement text of up to 600 characters."))

        now = datetime.now(UTC)

        def correct(state: MemoryState) -> None:
            previous = next((fact for fact in state.facts if fact.fact_id == fact_id), None)
            if (
                previous is None
                or not can_correct_memory(previous, user_tid, state.excluded_users)
                or previous.topic_id != self.event.message_thread_id
            ):
                raise ValueError("Only your own personal statements can be corrected")
            capture_source(
                state,
                MemorySource(
                    message_id=self.event.message_id,
                    speaker_tid=user_tid,
                    speaker_name=sender.full_name,
                    username=sender.username,
                    text=self.event.text or text,
                    sent_at=now,
                    topic_id=self.event.message_thread_id,
                    reply_to_message_id=self.event.reply_to_message.message_id if self.event.reply_to_message else None,
                    branch_id=self.event.message_id,
                ),
            )
            apply_actions(
                state,
                MemoryActions(
                    actions=[
                        MemoryAction(
                            action="UPDATE",
                            target_id=fact_id,
                            speaker_tid=user_tid,
                            subject_tids=[user_tid],
                            scope=previous.scope,
                            layer=previous.layer,
                            kind=previous.kind,
                            event=previous.event,
                            text=text,
                            evidence_ids=[self.event.message_id],
                        )
                    ]
                ),
                {self.event.message_id},
                state.generation,
                now,
            )

        try:
            await mutate_memory(chat, correct, redis=self.services.redis)
        except ValueError:
            return await self.event.reply(
                _("Use a current fact ID for your own personal statement in its original topic. Enable capture first.")
            )
        await reset_modern_context(chat.tid, redis=self.services.redis, clear_messages=False)
        await reset_tool_exchanges(chat.tid, redis=self.services.redis)
        return await self.event.reply(
            _("Your correction was saved with its source. The previous version remains in history.")
        )


class _AIMemoryClear(_AIMemoryCommand):
    operation: ClassVar[Literal["forget", "off"]]

    async def run(self, sender: User) -> Message:
        chat = self.connection.db_model
        if not await is_user_admin(chat.tid, sender.id):
            return await self.event.reply(_("Only chat administrators can clear shared AI memory and context."))
        data = AIMemoryClearCallback(
            owner_tid=sender.id, operation=self.operation, token=token_urlsafe(8), action="confirm"
        )
        await self.services.redis.set(_clear_key(chat.tid, self.event.chat.id, data), "1", ex=_CLEAR_TTL_SECONDS)
        prompt = _("Clear shared AI memory and context for everyone in this chat?")
        if self.operation == "off":
            prompt += "\n" + _("This also stops AI memory capture for you.")
        buttons = [
            InlineKeyboardButton(text=_("Clear shared memory"), callback_data=data.pack(), style="danger"),
            InlineKeyboardButton(text=_("Cancel"), callback_data=data.model_copy(update={"action": "cancel"}).pack()),
        ]
        return await self.event.reply(prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]))


@flags.handler_help(group=_MEMORY_HELP_GROUP, only_admin=True, description=l_("Clear shared AI memory and context."))
class AIMemoryForget(_AIMemoryClear):
    command = MemoryCommand.forget
    operation = "forget"


@flags.handler_help(
    group=_MEMORY_HELP_GROUP,
    only_admin=True,
    description=l_("Clear shared memory and stop saving your messages."),
)
class AIMemoryOff(_AIMemoryClear):
    command = MemoryCommand.off
    operation = "off"


@flags.handler_help(group=_MEMORY_HELP_GROUP, description=l_("Allow future capture of your messages."))
class AIMemoryOn(_AIMemoryCommand):
    command = MemoryCommand.on

    async def run(self, sender: User) -> Message:
        def opt_in(state: MemoryState) -> None:
            state.excluded_users = [identity for identity in state.excluded_users if identity != sender.id]

        await mutate_memory(self.connection.db_model, opt_in, redis=self.services.redis)
        return await self.event.reply(
            _("Your messages can be saved again when AI memory capture is enabled in this chat.")
        )


async def _memory_page(
    chat: ChatModel,
    browser: MemoryBrowser,
    data: AIMemoryPageCallback,
    *,
    redis: Redis,
) -> tuple[Doc, InlineKeyboardMarkup]:
    if data.target == "help":
        return memory_help(), memory_back_markup(data)
    state = await read_memory(chat, await memory_cutoff(chat.tid, redis=redis), redis=redis)
    corrections_enabled = bool(
        browser.member is None
        and data.view == "show"
        and data.target
        and (await resolve_chat_capabilities(chat)).memory
    )
    return memory_view(chat, state, browser, data, corrections_enabled=corrections_enabled)


class AIMemoryPageHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AIMemoryPageCallback.filter(), FeatureFlagFilter("ai_memory_controls")

    async def handle(self) -> None:
        callback = self.event
        data: AIMemoryPageCallback = self.data["callback_data"]
        if not isinstance(callback.message, Message):
            await callback.answer(_("This memory list is unavailable. Run /ai_memory again."), show_alert=True)
            return
        chat = self.connection.db_model
        redis = self.services.redis
        raw_browser = await redis.get(_browser_key(chat.tid, callback.message.chat.id, data.token))
        if raw_browser is None:
            await callback.answer(_("This memory list has expired. Run /ai_memory again."), show_alert=True)
            return
        browser = MemoryBrowser.model_validate_json(raw_browser)
        if callback.from_user.id != browser.owner_tid:
            await callback.answer(
                _("These memory controls belong to another user. Run /ai_memory yourself."), show_alert=True
            )
            return
        if browser.member is not None and data.view != "show":
            await callback.answer(_("This view only shows the member's current memories."), show_alert=True)
            return
        document, markup = await _memory_page(chat, browser, data, redis=redis)
        await common_try(self.answer_rich(document, reply_markup=markup))
        await callback.answer()


class AIMemoryClearHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AIMemoryClearCallback.filter(), FeatureFlagFilter("ai_memory_controls")

    async def handle(self) -> None:
        callback = self.event
        data: AIMemoryClearCallback = self.data["callback_data"]
        if callback.from_user.id != data.owner_tid:
            await callback.answer(_("Only the requester can use this confirmation."), show_alert=True)
            return
        if not isinstance(callback.message, Message):
            await callback.answer(_("This memory confirmation is unavailable."), show_alert=True)
            return
        chat = self.connection.db_model
        if data.action == "confirm" and not await is_user_admin(chat.tid, data.owner_tid):
            await callback.answer(_("Only chat administrators can clear shared AI memory."), show_alert=True)
            return
        redis = self.services.redis
        if not await redis.getdel(_clear_key(chat.tid, callback.message.chat.id, data)):
            await callback.answer(_("This memory confirmation is no longer active."), show_alert=True)
            return
        if data.action == "cancel":
            await reply_or_edit(callback, _("AI memory clear canceled."))
            await callback.answer()
            return

        def clear(state: MemoryState) -> None:
            # Other members' text can include indirect copies of the requester's information.
            clear_memory(state)
            if data.operation == "off" and data.owner_tid not in state.excluded_users:
                state.excluded_users.append(data.owner_tid)

        await mutate_memory(chat, clear, redis=redis)
        await ModernContext.reset(chat.tid, redis=redis)
        await reset_tool_exchanges(chat.tid, redis=redis)
        await AIMemoryModel.clear(chat.iid)
        await AIChatSummaryModel.find(AIChatSummaryModel.chat.id == chat.iid).delete()
        result = _("Shared AI memory and context were cleared.")
        if data.operation == "off":
            result += "\n" + _("AI memory capture is now off for you.")
        await reply_or_edit(callback, result)
        await callback.answer()
