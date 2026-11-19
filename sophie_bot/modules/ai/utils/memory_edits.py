from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from aiogram.types import Message
from redis.asyncio import Redis

from sophie_bot.db.models.ai.ai_evidence_memory import (
    MemoryAction,
    MemoryActions,
    MemoryFact,
    MemorySource,
    MemoryState,
)
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.ai.utils.cache_messages import reset_modern_context
from sophie_bot.modules.ai.utils.chatbot_tool_history import reset_tool_exchanges
from sophie_bot.modules.ai.utils.evidence_memory import (
    apply_actions,
    capture_source,
    memory_cutoff,
    mutate_memory,
    prune_memory,
)
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _

MemoryEdit = Literal["add", "update", "delete"]
MAX_DELETE_IDS = 20


class MemoryEditRejected(ValueError):
    pass


@dataclass(slots=True)
class MemoryEditRequest:
    source: MemorySource
    epoch: int
    outcome: MemoryEdit | None = None

    @classmethod
    def from_message(cls, message: Message, epoch: int) -> MemoryEditRequest | None:
        sender = message.from_user
        raw_text = message.text or message.caption
        if sender is None or sender.is_bot or message.sender_chat or message.forward_origin or not raw_text:
            return None
        return cls(
            MemorySource(
                message_id=message.message_id,
                speaker_tid=sender.id,
                speaker_name=sender.full_name,
                username=sender.username,
                text=raw_text,
                # A timestamp ahead of the local clock is invisible to cache reads.
                sent_at=min(message.date.astimezone(UTC), datetime.now(UTC)),
                topic_id=message.message_thread_id,
                reply_to_message_id=message.reply_to_message.message_id if message.reply_to_message else None,
                branch_id=message.message_id,
            ),
            epoch,
        )


async def chatbot_memory_edits_enabled(chat_tid: int, *, redis: Redis) -> bool:
    return await is_enabled("ai_memory_chatbot_edits", chat_tid=chat_tid, redis=redis) and await is_enabled(
        "ai_memory_retrieval", chat_tid=chat_tid, redis=redis
    )


def _owns_fact(fact: MemoryFact, actor_tid: int) -> bool:
    return (
        fact.scope == "person"
        and fact.layer == "personal"
        and fact.speaker_tid == actor_tid
        and fact.subject_tids == [actor_tid]
    )


def _validate_fact_ids(fact_ids: list[str]) -> None:
    if not 1 <= len(fact_ids) <= MAX_DELETE_IDS or any(
        not re.fullmatch(r"[0-9a-f]{32}", fact_id) for fact_id in fact_ids
    ):
        raise MemoryEditRejected(_("Enter up to 20 valid memory fact IDs, separated by spaces."))


async def _invalidate_memory_views(chat_tid: int, *, redis: Redis) -> None:
    await reset_modern_context(chat_tid, redis=redis, clear_messages=False)
    await reset_tool_exchanges(chat_tid, redis=redis)


async def delete_memory_facts(
    chat: ChatModel,
    fact_ids: list[str],
    actor_tid: int,
    *,
    admin: bool,
    redis: Redis,
    expected_epoch: int,
) -> None:
    _validate_fact_ids(fact_ids)
    cutoff = await memory_cutoff(chat.tid, redis=redis)

    def delete(state: MemoryState) -> None:
        prune_memory(state, cutoff)
        facts = {fact.fact_id: fact for fact in state.facts}
        targets = set(fact_ids)
        if not targets <= facts.keys():
            raise MemoryEditRejected(_("Some of those memory IDs were not found in this chat."))
        # Deleting only the head would make an older version current again.
        roots: dict[str, str] = {}
        for fact in state.facts:
            roots[fact.fact_id] = roots.get(fact.previous_id, fact.fact_id) if fact.previous_id else fact.fact_id
        chains = {roots[fact_id] for fact_id in targets}
        removed = [fact for fact in state.facts if roots[fact.fact_id] in chains]
        if not admin and any(not _owns_fact(fact, actor_tid) for fact in removed):
            raise MemoryEditRejected(_("Only a chat administrator can delete another person's or shared memories."))
        state.facts = [fact for fact in state.facts if roots[fact.fact_id] not in chains]
        evidence_ids = {message_id for fact in removed for message_id in fact.evidence_ids}
        for source in state.sources:
            if source.message_id in evidence_ids:
                source.processed = True
        state.generation = uuid4().hex

    await mutate_memory(chat, delete, redis=redis, expected_epoch=expected_epoch, preserve_history=True)
    await _invalidate_memory_views(chat.tid, redis=redis)


async def save_memory_fact(
    chat: ChatModel,
    request: MemoryEditRequest,
    text: str,
    *,
    event: str = "",
    subject_tid: int | None = None,
    fact_id: str | None = None,
    admin: bool,
    redis: Redis,
) -> None:
    source = request.source
    actor_tid = source.speaker_tid
    text = text.strip()
    event = event.strip()
    if not text or len(text) > 600:
        raise MemoryEditRejected(_("Use between 1 and 600 characters for the memory text."))
    if fact_id is None and (not event or len(event) > 120):
        raise MemoryEditRejected(_("Give the memory a short topic name."))
    cutoff = await memory_cutoff(chat.tid, redis=redis)

    def save(state: MemoryState) -> None:
        prune_memory(state, cutoff)
        if actor_tid in state.excluded_users:
            raise MemoryEditRejected(_("Memory capture is off for you."))
        previous = next((fact for fact in state.facts if fact.fact_id == fact_id), None)
        if fact_id is not None and previous is None:
            raise MemoryEditRejected(_("That memory ID was not found in this chat."))
        if previous is not None:
            if not admin and not _owns_fact(previous, actor_tid):
                raise MemoryEditRejected(_("Only a chat administrator can update another person's memory."))
            if previous.scope != "person" or previous.topic_id != source.topic_id:
                raise MemoryEditRejected(_("Update personal memories in their original topic."))
            if any(fact.previous_id == previous.fact_id for fact in state.facts):
                raise MemoryEditRejected(_("Use the ID of the current memory version."))
            if source.sent_at < previous.occurred_at:
                raise MemoryEditRejected(_("Send a new request to update this memory version."))
            if set(previous.subject_tids + [previous.speaker_tid]).intersection(state.excluded_users):
                raise MemoryEditRejected(_("Memory capture is off for someone in that memory."))
            action = MemoryAction(
                action="UPDATE",
                target_id=previous.fact_id,
                speaker_tid=previous.speaker_tid,
                subject_tids=previous.subject_tids,
                scope=previous.scope,
                layer=previous.layer,
                kind=previous.kind,
                event=previous.event,
                text=text,
                evidence_ids=[source.message_id],
            )
        else:
            subject = actor_tid if subject_tid is None else subject_tid
            if subject != actor_tid and not admin:
                raise MemoryEditRejected(_("Only a chat administrator can add a memory for another person."))
            if subject in state.excluded_users:
                raise MemoryEditRejected(_("Memory capture is off for that person."))
            if subject != actor_tid and not (
                any(item.speaker_tid == subject for item in state.sources)
                or any(subject == fact.speaker_tid or subject in fact.subject_tids for fact in state.facts)
            ):
                raise MemoryEditRejected(_("That person has no attributed memory evidence in this chat."))
            action = MemoryAction(
                action="ADD",
                speaker_tid=actor_tid,
                subject_tids=[subject],
                layer="personal" if subject == actor_tid else "observation",
                event=event,
                text=text,
                evidence_ids=[source.message_id],
            )
        existing = next((item for item in state.sources if item.message_id == source.message_id), None)
        if existing is not None and (existing.processed or existing.text != source.text):
            raise MemoryEditRejected(_("That request was already processed. Send a new message to change memory."))
        if existing is None:
            capture_source(state, source.model_copy())
        editor = actor_tid if action.speaker_tid != actor_tid else None
        if not apply_actions(
            state,
            MemoryActions(actions=[action]),
            {source.message_id},
            state.generation,
            datetime.now(UTC),
            editor_tid=editor,
        ):
            raise MemoryEditRejected(_("The memory changed while processing. Try again."))
        prune_memory(state, cutoff)
        state.generation = uuid4().hex

    await mutate_memory(chat, save, redis=redis, expected_epoch=request.epoch, preserve_history=True)
    await _invalidate_memory_views(chat.tid, redis=redis)
