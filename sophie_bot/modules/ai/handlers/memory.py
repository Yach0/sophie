from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Code, Doc, KeyValue, Section, Template, VList

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryModel
from sophie_bot.db.models.ai.ai_evidence_memory import MemoryAction, MemoryActions, MemorySource, MemoryState
from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.utils.ai_mode import resolve_chat_capabilities
from sophie_bot.modules.ai.utils.chatbot_tool_history import reset_tool_exchanges
from sophie_bot.modules.ai.utils.evidence_memory import (
    apply_actions,
    capture_source,
    clear_memory,
    memory_cutoff,
    mutate_memory,
    read_memory,
)
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def _memory_examples() -> Section:
    return Section(
        VList(
            KeyValue(Code("/ai_memory show"), _("Inspect your facts and find their IDs.")),
            KeyValue(Code("/ai_memory show 2"), _("Show the second page of facts.")),
            KeyValue(Code("/ai_memory sources 2"), _("Show the second page of source messages.")),
            KeyValue(
                Code(Template("/ai_memory correct FACT_ID {text}", text=_("I prefer short replies."))),
                _("Replace FACT_ID with a current ID from show. Send the command in the original chat and topic."),
            ),
            KeyValue(Code("/ai_memory forget"), _("Admins: clear shared chat memory; allow future capture.")),
            KeyValue(Code("/ai_memory off"), _("Admins: clear shared chat memory and stop capturing your messages.")),
            KeyValue(Code("/ai_memory on"), _("Allow future capture without restoring deleted information.")),
        ),
        title=_("Examples"),
    )


@flags.handler_help(description=l_("Inspect, correct, or forget your information in this chat's AI memory."))
class AIMemoryControl(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("ai_memory"), FeatureFlagFilter("ai_memory_controls")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, Any]) -> dict[str, ArgFabric]:
        return {"request": OptionalArg(TextArg(l_("Memory operation and arguments")))}

    async def handle(self) -> Message:
        chat = self.connection.db_model
        sender = self.event.from_user
        if sender is None or self.event.sender_chat:
            return await self.event.reply(_("Send this command as yourself to manage your memory."))
        user_tid = sender.id
        request = (self.data.get("request") or "show").strip()
        operation, _separator, argument = request.partition(" ")
        if operation in {"show", "sources"}:
            try:
                page = max(1, int(argument or "1"))
            except ValueError:
                return await self.event.reply(
                    Doc(
                        _("Use a page number, starting at 1."), Code("/ai_memory show 2"), Code("/ai_memory sources 2")
                    ).to_html()
                )
            state = await read_memory(chat.iid, await memory_cutoff(chat.tid, redis=self.services.redis))
            doc = Doc(_("Your AI memory in this chat:"))
            start = (page - 1) * 3
            if operation == "show":
                own = [fact for fact in state.facts if user_tid == fact.speaker_tid or user_tid in fact.subject_tids]
                for fact in own[start : start + 3]:
                    doc += Section(
                        VList(
                            KeyValue(_("ID"), Code(fact.fact_id)),
                            KeyValue(_("Text"), fact.text),
                            KeyValue(_("Speaker"), Code(str(fact.speaker_tid))),
                            KeyValue(
                                _("Source messages"), Code(", ".join(str(identity) for identity in fact.evidence_ids))
                            ),
                            KeyValue(_("Previous version"), Code(fact.previous_id or "-")),
                            KeyValue(_("Time"), fact.occurred_at.isoformat()),
                        ),
                        title=_("Memory"),
                    )
                count = len(own)
            else:
                own_sources = [source for source in state.sources if source.speaker_tid == user_tid]
                source_parts = [
                    (source, source.text[offset : offset + 600])
                    for source in own_sources
                    for offset in range(0, len(source.text), 600)
                ]
                for source, excerpt in source_parts[start : start + 3]:
                    doc += Section(
                        VList(
                            KeyValue(_("ID"), Code(str(source.message_id))),
                            KeyValue(_("Time"), source.sent_at.isoformat()),
                            KeyValue(_("Text"), excerpt),
                        ),
                        title=_("Source message"),
                    )
                count = len(source_parts)
            doc += VList(
                KeyValue(_("Page"), Template(_("{page} of {pages}"), page=page, pages=max(1, (count + 2) // 3))),
                KeyValue(_("Memory capture disabled"), _("Yes") if user_tid in state.excluded_users else _("No")),
            )
            if not self.data.get("request") or count == 0:
                doc += _memory_examples()
            return await self.event.reply(doc.to_html())
        if operation in {"forget", "off", "on"}:
            if operation in {"forget", "off"} and not await is_user_admin(chat.tid, user_tid):
                return await self.event.reply(
                    _(
                        "Only chat administrators can use forget or off because these commands clear shared chat memory and context."
                    )
                )

            def change(state: MemoryState) -> None:
                if operation != "on":
                    # Other members' verbatim text can mention this person. Clear the chat evidence
                    # rather than retain indirect copies that cannot be identified reliably.
                    clear_memory(state)
                if operation == "off" and user_tid not in state.excluded_users:
                    state.excluded_users.append(user_tid)
                elif operation == "on":
                    state.excluded_users = [identity for identity in state.excluded_users if identity != user_tid]

            await mutate_memory(chat, change)
            if operation != "on":
                await self._clear_context()
            return await self.event.reply(
                _(
                    "Your memory control was applied. Forget and off also clear chat evidence and AI context to remove indirect copies."
                )
            )
        if operation == "correct":
            if not (await resolve_chat_capabilities(chat)).memory:
                return await self.event.reply(_("Memory capture requires entertainment mode in this chat."))
            if self.event.chat.id != chat.tid:
                return await self.event.reply(_("Send the correction in the original chat and topic."))
            fact_id, _separator, text = argument.partition(" ")
            if not text.strip() or len(text) > 600:
                return await self.event.reply(
                    Doc(_("Use /ai_memory correct FACT_ID TEXT (up to 600 characters)."), _memory_examples()).to_html()
                )
            now = datetime.now(UTC)

            def correct(state: MemoryState) -> None:
                previous = next((fact for fact in state.facts if fact.fact_id == fact_id), None)
                if (
                    previous is None
                    or previous.speaker_tid != user_tid
                    or previous.subject_tids != [user_tid]
                    or previous.scope != "person"
                    or user_tid in state.excluded_users
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
                        reply_to_message_id=self.event.reply_to_message.message_id
                        if self.event.reply_to_message
                        else None,
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
                await mutate_memory(chat, correct)
            except ValueError:
                return await self.event.reply(
                    _(
                        "Use a current fact ID for your own personal statement in its original topic. Enable capture first."
                    )
                )
            await ModernContext.reset(chat.tid, redis=self.services.redis)
            await reset_tool_exchanges(chat.tid, redis=self.services.redis)
            return await self.event.reply(
                _("Your correction was saved with its source. The previous version remains in history.")
            )
        return await self.event.reply(
            Doc(
                _("Use /ai_memory show [PAGE], sources [PAGE], forget, off, on, or correct FACT_ID TEXT."),
                _memory_examples(),
            ).to_html()
        )

    async def _clear_context(self) -> None:
        chat = self.connection.db_model
        await ModernContext.reset(chat.tid, redis=self.services.redis)
        await reset_tool_exchanges(chat.tid, redis=self.services.redis)
        await AIMemoryModel.clear(chat.iid)
        await AIChatSummaryModel.find(AIChatSummaryModel.chat.id == chat.iid).delete()
