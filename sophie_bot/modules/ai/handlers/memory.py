from __future__ import annotations

from abc import abstractmethod
from re import fullmatch
from secrets import token_urlsafe
from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import ForceReply, Message, User
from ass_tg.types import OptionalArg, OrArg, TextArg, UserMentionArg
from redis.asyncio import Redis
from stfu_tg import Button, ButtonRow, Doc, Template, UserLink

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryModel
from sophie_bot.db.models.ai.ai_evidence_memory import MemoryState
from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.callbacks import (
    AIMemoryClearCallback,
    AIMemoryEditCallback,
    AIMemoryPageCallback,
    MemoryListView,
)
from sophie_bot.modules.ai.utils.ai_mode import resolve_chat_capabilities
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message
from sophie_bot.modules.ai.utils.cache_messages import get_context_epoch_key
from sophie_bot.modules.ai.utils.chatbot_tool_history import reset_tool_exchanges
from sophie_bot.modules.ai.utils.evidence_memory import (
    MemoryContextChanged,
    clear_memory,
    memory_cutoff,
    mutate_memory,
    read_memory,
)
from sophie_bot.modules.ai.utils.memory_edits import (
    MemoryEditProposal,
    MemoryEditRejected,
    MemoryEditRequest,
    apply_memory_edit,
    chatbot_memory_edits_enabled,
    delete_memory_facts,
    memory_edit_key,
    validate_memory_update,
)
from sophie_bot.modules.ai.utils.memory_view import (
    MemoryBrowser,
    MemoryMember,
    memory_facts,
    memory_view,
)
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.reply_or_edit import reply_or_edit
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

_BROWSE_TTL_SECONDS = 15 * 60
_CLEAR_TTL_SECONDS = 5 * 60
_EDIT_REPLY_TTL_SECONDS = 5 * 60


def _browser_key(chat_tid: int, message_chat_tid: int, token: str) -> str:
    return f"ai:memory:browse:{chat_tid}:{message_chat_tid}:{token}"


def _clear_key(chat_tid: int, message_chat_tid: int, data: AIMemoryClearCallback) -> str:
    return f"ai:memory:clear:{chat_tid}:{message_chat_tid}:{data.owner_tid}:{data.operation}:{data.token}"


def _edit_reply_key(chat_tid: int, user_tid: int, prompt_id: int) -> str:
    return f"ai:memory:edit-reply:{chat_tid}:{user_tid}:{prompt_id}"


async def _memory_privacy(chat: ChatModel, user_tid: int, data: AIMemoryPageCallback, *, redis: Redis) -> Doc:
    state = await read_memory(chat, await memory_cutoff(chat.tid), redis=redis)
    excluded = user_tid in state.excluded_users
    document = Doc(_("Memory capture is off for you.") if excluded else _("You allow AI memory capture in this chat."))
    if excluded:
        document += ButtonRow(
            Button(_("Resume saving my messages"), callback_data=data.model_copy(update={"target": "resume"}).pack())
        )
    if await is_user_admin(chat.tid, user_tid):
        document += ButtonRow(
            Button(_("Clear shared memory"), callback_data=data.model_copy(update={"target": "forget"}).pack())
        )
        if not excluded:
            document += ButtonRow(
                Button(_("Stop saving my messages"), callback_data=data.model_copy(update={"target": "off"}).pack())
            )
    return document


_MEMORY_HELP_GROUP = l_("Memory")


@flags.ai_cache(exclude_user_command=True)
class _AIMemoryCommand(SophieMessageHandler):
    command: ClassVar[str]

    @classmethod
    def filters(cls) -> tuple[CallbackType, ...]:
        return CMDFilter(cls.command), FeatureFlagFilter("ai_memory_controls")

    async def handle(self) -> Message:
        sender = self.event.from_user
        if sender is None or self.event.sender_chat:
            return await self.event.reply(_("Send this command as yourself to manage your memory."))
        return await self.run(sender)

    @abstractmethod
    async def run(self, sender: User) -> Message:
        pass


@flags.handler_help(
    group=_MEMORY_HELP_GROUP,
    description=l_(
        "Browse your memories, search by text, or view a member's memories by mention or reply. "
        "Use --history, --sources, or --privacy to open those views."
    ),
)
@flags.args(query=OptionalArg(OrArg(UserMentionArg(), TextArg(), description=l_("?@username or search text"))))
class AIMemoryBrowse(_AIMemoryCommand):
    command = "ai_memory"

    async def run(self, sender: User) -> Message:
        query: str | User | None = self.data.get("query")
        search = ""
        member = None
        view: MemoryListView = "show"
        privacy = False
        if isinstance(query, User):
            member = MemoryMember(tid=query.id, name=query.full_name)
        elif query:
            search = " ".join(query.split())
            option, _separator, remainder = search.partition(" ")
            if option == "--history":
                view, search = "history", remainder
            elif search == "--sources":
                view, search = "sources", ""
            elif search == "--privacy":
                privacy, search = True, ""
            elif search in {"show", "sources", "forget", "off", "on"}:
                return await self.event.reply(
                    _("Use /ai_memory to browse memories or /ai_memory --privacy for privacy.")
                )
            elif fullmatch(r"correct(?: [0-9a-f]{32}(?: .*)?)?", search):
                return await self.event.reply(_("Ask Sophie in chat to correct a saved memory."))
            elif fullmatch(r"@[A-Za-z0-9_]{1,32}", search):
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
        browser = MemoryBrowser(owner_tid=sender.id, search=search, member=member)
        chat = self.connection.db_model
        redis = self.services.redis
        data = AIMemoryPageCallback(view=view, token=token_urlsafe(8))
        await redis.set(
            _browser_key(chat.tid, self.event.chat.id, data.token), browser.model_dump_json(), ex=_BROWSE_TTL_SECONDS
        )
        if privacy:
            return await send_ai_rich_message(self.event, await _memory_privacy(chat, sender.id, data, redis=redis))
        state = await read_memory(chat, await memory_cutoff(chat.tid), redis=redis)
        document, markup = memory_view(state, browser, data)
        return await send_ai_rich_message(self.event, document, reply_markup=markup)


@flags.handler_help(
    group=_MEMORY_HELP_GROUP,
    description=l_("Delete saved memories by fact ID. Open Details on a memory to find its ID."),
)
@flags.args(fact_ids=TextArg(l_("Memory IDs separated by spaces")))
class AIMemoryDelete(_AIMemoryCommand):
    command = "ai_memory_delete"

    async def run(self, sender: User) -> Message:
        if sender.is_bot or self.event.forward_origin:
            return await self.event.reply(_("Send a new message yourself to delete saved memories."))
        chat = self.connection.db_model
        redis = self.services.redis
        fact_ids: str = self.data["fact_ids"]
        try:
            await delete_memory_facts(
                chat,
                fact_ids.split(),
                sender.id,
                admin=await is_user_admin(chat.tid, sender.id),
                redis=redis,
            )
        except MemoryContextChanged:
            return await self.event.reply(_("Memory changed while processing. Please try again."))
        except MemoryEditRejected as error:
            return await self.event.reply(str(error))
        return await self.event.reply(_("I deleted the selected memories and their previous versions."))


class AIMemoryEditHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AIMemoryEditCallback.filter(), FeatureFlagFilter("ai_memory_controls")

    async def handle(self) -> None:
        callback = self.event
        data: AIMemoryEditCallback = self.data["callback_data"]
        chat = self.connection.db_model
        if not isinstance(callback.message, Message) or callback.message.chat.id != chat.tid:
            await callback.answer(_("Confirm this memory change in the original chat."), show_alert=True)
            return
        redis = self.services.redis
        actor_tid = callback.from_user.id
        raw = await redis.getdel(memory_edit_key(chat.tid, actor_tid, data.token))
        if raw is None:
            await callback.answer(_("This proposal expired or belongs to another user."), show_alert=True)
            return
        if data.action == "cancel":
            await common_try(self.answer_rich(Doc(_("Memory change canceled.")), reply_markup=None))
            await callback.answer()
            return
        proposal = MemoryEditProposal.model_validate_json(raw)
        if proposal.source.speaker_tid != actor_tid or proposal.source.topic_id != callback.message.message_thread_id:
            await callback.answer(_("Confirm this memory change in the original topic."), show_alert=True)
            return
        if (
            not await chatbot_memory_edits_enabled(chat.tid, redis=redis)
            or not (await resolve_chat_capabilities(chat)).memory
        ):
            await callback.answer(_("Chatbot memory edits are disabled in this chat."), show_alert=True)
            return
        try:
            await apply_memory_edit(chat, proposal, admin=await is_user_admin(chat.tid, actor_tid), redis=redis)
        except MemoryContextChanged:
            await callback.answer(_("Memory changed after this proposal. Ask again."), show_alert=True)
            return
        except MemoryEditRejected as error:
            await callback.answer(str(error), show_alert=True)
            return
        text = {
            "add": _("I saved that memory."),
            "update": _("I updated that memory. The previous version is still in history."),
            "delete": _("I deleted the selected memories and their previous versions."),
        }[proposal.action]
        await common_try(self.answer_rich(Doc(text), reply_markup=None))
        await callback.answer()


class AIMemoryPageHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AIMemoryPageCallback.filter(), FeatureFlagFilter("ai_memory_controls")

    async def _privacy(self, chat: ChatModel, data: AIMemoryPageCallback, message: Message) -> None:
        callback = self.event
        redis = self.services.redis
        user_tid = callback.from_user.id
        resume = data.target == "resume"
        if resume:

            def opt_in(state: MemoryState) -> None:
                state.excluded_users = [identity for identity in state.excluded_users if identity != user_tid]

            await mutate_memory(chat, opt_in, redis=redis)

        if data.target in {"forget", "off"}:
            if not await is_user_admin(chat.tid, user_tid):
                await callback.answer(_("Only chat administrators can clear shared AI memory."), show_alert=True)
                return
            confirmation = AIMemoryClearCallback(
                owner_tid=user_tid,
                operation="off" if data.target == "off" else "forget",
                token=token_urlsafe(8),
                action="confirm",
            )
            await redis.set(_clear_key(chat.tid, message.chat.id, confirmation), "1", ex=_CLEAR_TTL_SECONDS)
            prompt = Doc(_("Clear shared AI memory and context for everyone in this chat?"))
            if confirmation.operation == "off":
                prompt += _("This also stops AI memory capture for you.")
            prompt += ButtonRow(
                Button(_("Clear shared memory"), callback_data=confirmation.pack(), style="danger"),
                Button(_("Cancel"), callback_data=confirmation.model_copy(update={"action": "cancel"}).pack()),
            )
            await common_try(self.answer_rich(prompt, reply_markup=None))
            await callback.answer()
            return

        document = await _memory_privacy(chat, user_tid, data, redis=redis)
        await common_try(self.answer_rich(document, reply_markup=None))
        if resume:
            await callback.answer(
                _("Your messages can be saved again when AI memory capture is enabled."), show_alert=True
            )
        else:
            await callback.answer()

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
        privacy = data.target in {"privacy", "resume", "forget", "off"}
        if browser.member is not None and (data.view != "show" or privacy):
            await callback.answer(_("This view only shows the member's current memories."), show_alert=True)
            return
        if privacy:
            await self._privacy(chat, data, callback.message)
            return
        state = await read_memory(chat, await memory_cutoff(chat.tid), redis=redis)
        if data.edit:
            await self._start_edit(chat, callback.message, browser, data, state)
            return
        document, markup = memory_view(state, browser, data)
        if data.target and data.part is None:
            await send_ai_rich_message(callback.message, document, reply_markup=markup)
        else:
            await common_try(self.answer_rich(document, reply_markup=markup))
        await callback.answer()

    async def _start_edit(
        self, chat: ChatModel, message: Message, browser: MemoryBrowser, data: AIMemoryPageCallback, state: MemoryState
    ) -> None:
        callback = self.event
        redis = self.services.redis
        actor_tid = callback.from_user.id
        if message.chat.id != chat.tid or data.view != "show":
            await callback.answer(_("Open the current memory in its original chat to edit it."), show_alert=True)
            return
        if (
            not await chatbot_memory_edits_enabled(chat.tid, redis=redis)
            or not (await resolve_chat_capabilities(chat)).memory
        ):
            await callback.answer(_("Memory edits are disabled in this chat."), show_alert=True)
            return
        fact = next((fact for fact in memory_facts(state, browser, data.view) if fact.fact_id == data.target), None)
        if fact is None:
            await callback.answer(_("This memory is no longer in this list."), show_alert=True)
            return
        try:
            validate_memory_update(
                fact, actor_tid, message.message_thread_id, admin=await is_user_admin(chat.tid, actor_tid)
            )
        except MemoryEditRejected as error:
            await callback.answer(str(error), show_alert=True)
            return
        prompt = await message.reply(
            Template(
                _("{user}, reply to this message with the new memory text."),
                user=UserLink(actor_tid, callback.from_user.full_name),
            ).to_html(),
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        await redis.set(
            _edit_reply_key(chat.tid, actor_tid, prompt.message_id), fact.fact_id, ex=_EDIT_REPLY_TTL_SECONDS
        )
        await callback.answer()


class AIMemoryEditReply(SophieMessageHandler):
    @staticmethod
    async def _edit_reply(message: Message, services: ApplicationServices) -> bool | dict[str, str]:
        sender = message.from_user
        parent = message.reply_to_message
        if not sender or sender.is_bot or message.sender_chat or message.forward_origin or not message.text:
            return False
        if not parent or not is_real_reply(message):
            return False
        key = _edit_reply_key(message.chat.id, sender.id, parent.message_id)
        raw = await services.redis.get(key)
        if raw is None:
            return False
        return {"memory_fact_id": raw.decode() if isinstance(raw, bytes) else raw, "memory_edit_reply_key": key}

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            FeatureFlagFilter("ai_memory_controls"),
            FeatureFlagFilter("ai_memory_retrieval"),
            AIMemoryEditReply._edit_reply,
        )

    async def handle(self) -> Message | None:
        chat = self.connection.db_model
        redis = self.services.redis
        if chat.tid != self.event.chat.id or not (await resolve_chat_capabilities(chat)).memory:
            return await self.event.reply(_("Memory edits are disabled in this chat."))
        epoch = int(await redis.get(get_context_epoch_key(chat.tid)) or 0)
        request = MemoryEditRequest.from_message(self.event, epoch)
        if request is None:
            return None
        proposal = MemoryEditProposal(
            source=request.source,
            epoch=epoch,
            action="update",
            text=request.source.text,
            fact_ids=[self.data["memory_fact_id"]],
        )
        try:
            admin = await is_user_admin(chat.tid, request.source.speaker_tid)
            await apply_memory_edit(chat, proposal, admin=admin, redis=redis)
        except MemoryContextChanged:
            return await self.event.reply(_("Memory changed while processing. Please try again."))
        except MemoryEditRejected as error:
            return await self.event.reply(str(error))
        await redis.delete(self.data["memory_edit_reply_key"])
        return await self.event.reply(_("I updated that memory. The previous version is still in history."))


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
