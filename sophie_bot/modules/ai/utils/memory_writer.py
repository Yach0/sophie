from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from sophie_bot.db.models.ai.ai_evidence_memory import (
    MemoryAction,
    MemoryActions,
    MemoryFact,
    MemoryKind,
    MemoryLayer,
    MemorySource,
    MemoryState,
)
from sophie_bot.modules.ai.utils.ai_tokens import TokenCounter

# Reserve room outside the data budget for instructions, schema and request framing.
WRITER_DATA_TOKENS = 8_000
_NEW_MESSAGES_TOKENS = 5_500
_MAX_HEADS = 12
_MAX_SUPPORT = 16


class WriterAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ADD", "UPDATE"]
    target_id: str | None = None
    speaker_tid: int
    subject_tids: list[int] = Field(max_length=30)
    scope: Literal["person", "group"]
    layer: MemoryLayer
    kind: MemoryKind
    event: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    evidence_ids: list[int] = Field(min_length=1, max_length=20)


class WriterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[WriterAction] = Field(max_length=60)


class RoutineMemoryOutput(WriterOutput):
    review: list[int] = Field(max_length=60)


@dataclass(frozen=True, slots=True)
class WriterPrompt:
    text: str
    batch_ids: frozenset[int]
    messages: dict[int, MemorySource]
    speakers: dict[int, int]
    heads: dict[str, MemoryFact]

    def decode_review(self, labels: Sequence[int]) -> set[int]:
        if any(label not in self.messages or self.messages[label].message_id not in self.batch_ids for label in labels):
            raise ValueError("Memory writer asked to review a message outside the batch")
        return {self.messages[label].message_id for label in labels}

    def decode_actions(self, actions: Sequence[WriterAction]) -> list[MemoryAction]:
        decoded = []
        for action in actions:
            try:
                fact_id = self.heads[action.target_id].fact_id if action.target_id is not None else None
                speaker_tid = self.speakers[action.speaker_tid]
                subject_tids = [self.speakers[label] for label in action.subject_tids]
                evidence_ids = [self.messages[label].message_id for label in action.evidence_ids]
            except KeyError as error:
                raise ValueError("Memory writer used a reference outside its prompt") from error
            if not self.batch_ids.intersection(evidence_ids):
                raise ValueError("Memory writer action has no new evidence")
            decoded.append(
                MemoryAction(
                    action=action.action,
                    target_id=fact_id,
                    speaker_tid=speaker_tid,
                    subject_tids=subject_tids,
                    scope=action.scope,
                    layer=action.layer,
                    kind=action.kind,
                    event=action.event,
                    text=action.text,
                    evidence_ids=evidence_ids,
                )
            )
        return decoded


def deduplicate_batch(
    sources: Sequence[MemorySource], pending: Sequence[MemorySource]
) -> tuple[list[MemorySource], dict[int, int]]:
    pending_ids = {source.message_id for source in pending}
    latest: dict[tuple[int, int | None, int], MemorySource] = {}
    unique = []
    duplicates = {}
    for source in sources:
        location = (source.speaker_tid, source.topic_id, source.branch_id)
        previous = latest.get(location)
        if source.message_id in pending_ids:
            if previous and previous.text == source.text and previous.reply_to_message_id == source.reply_to_message_id:
                duplicates[source.message_id] = previous.message_id
                continue
            unique.append(source)
        if source.processed or source.message_id in pending_ids:
            latest[location] = source
        else:
            latest.pop(location, None)
    return unique, duplicates


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]{3,}", text.casefold()))


def _relevant_heads(state: MemoryState, batch: Sequence[MemorySource]) -> list[MemoryFact]:
    superseded = {fact.previous_id for fact in state.facts}
    terms: dict[int | None, set[str]] = defaultdict(set)
    branches: dict[int | None, set[int]] = defaultdict(set)
    people: dict[int | None, set[int]] = defaultdict(set)
    for source in batch:
        terms[source.topic_id].update(_terms(source.text))
        branches[source.topic_id].add(source.branch_id)
        people[source.topic_id].add(source.speaker_tid)

    ranked = []
    excluded = set(state.excluded_users)
    for fact in state.facts:
        if (
            fact.fact_id in superseded
            or fact.topic_id not in terms
            or excluded.intersection([fact.speaker_tid, *fact.subject_tids])
        ):
            continue
        same_branch = bool(branches[fact.topic_id].intersection(fact.branch_ids))
        overlap = len(terms[fact.topic_id].intersection(_terms(f"{fact.event} {fact.text}")))
        same_person = bool(people[fact.topic_id].intersection([fact.speaker_tid, *fact.subject_tids]))
        if same_branch or overlap:
            ranked.append(((same_branch, same_person, overlap, fact.recorded_at), fact))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [fact for _, fact in ranked[:_MAX_HEADS]]


def _supporting_sources(
    state: MemoryState, batch: Sequence[MemorySource], heads: Sequence[MemoryFact]
) -> list[MemorySource]:
    batch_ids = {source.message_id for source in batch}
    parents = {source.reply_to_message_id for source in batch}
    head_ids = {identity for head in heads for identity in head.evidence_ids}
    branches = {(source.topic_id, source.branch_id) for source in batch}
    topics = {source.topic_id for source in batch}

    return sorted(
        (
            source
            for source in state.sources
            if source.message_id not in batch_ids
            and source.topic_id in topics
            and (
                source.message_id in parents
                or source.message_id in head_ids
                or (source.topic_id, source.branch_id) in branches
            )
        ),
        key=lambda source: (source.message_id in parents, source.message_id in head_ids, source.sent_at),
        reverse=True,
    )[:_MAX_SUPPORT]


def _project(
    batch: Sequence[MemorySource],
    support: Sequence[MemorySource],
    heads: Sequence[MemoryFact],
    names: dict[int, set[str]],
    excluded: set[int],
) -> WriterPrompt:
    sources = [*batch, *support]
    message_labels = {source.message_id: index for index, source in enumerate(sources, 1)}
    speaker_ids = dict.fromkeys(
        [source.speaker_tid for source in sources]
        + [identity for head in heads for identity in (head.speaker_tid, *head.subject_tids)]
        + list(names)
    )
    speaker_labels = {
        identity: index
        for index, identity in enumerate((identity for identity in speaker_ids if identity not in excluded), 1)
    }
    topic_labels = {
        topic_id: index for index, topic_id in enumerate(dict.fromkeys(source.topic_id for source in sources), 1)
    }
    branch_labels = {
        branch: index for index, branch in enumerate(dict.fromkeys(source.branch_id for source in sources), 1)
    }
    for head in heads:
        for branch in head.branch_ids:
            branch_labels.setdefault(branch, len(branch_labels) + 1)

    def message_row(source: MemorySource) -> list[object]:
        return [
            message_labels[source.message_id],
            speaker_labels[source.speaker_tid],
            topic_labels[source.topic_id],
            branch_labels[source.branch_id],
            message_labels.get(source.reply_to_message_id) if source.reply_to_message_id is not None else None,
            source.sent_at.isoformat(timespec="seconds"),
            source.text,
        ]

    fact_labels = {str(index): fact for index, fact in enumerate(heads, 1)}
    payload = {
        "batch": [message_row(source) for source in batch],
        "support": [message_row(source) for source in support],
        "people": {label: sorted(names.get(identity, ())) for identity, label in speaker_labels.items()},
        "heads": [
            [
                label,
                speaker_labels.get(fact.speaker_tid),
                [speaker_labels[identity] for identity in fact.subject_tids if identity in speaker_labels],
                topic_labels[fact.topic_id],
                [branch_labels[identity] for identity in fact.branch_ids],
                fact.scope,
                fact.layer,
                fact.kind,
                fact.event,
                fact.text,
            ]
            for label, fact in fact_labels.items()
        ],
    }
    return WriterPrompt(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        frozenset(source.message_id for source in batch),
        {message_labels[source.message_id]: source for source in sources},
        {label: identity for identity, label in speaker_labels.items()},
        fact_labels,
    )


def build_writer_prompt(state: MemoryState, pending: Sequence[MemorySource], counter: TokenCounter) -> WriterPrompt:
    excluded = set(state.excluded_users)
    batch: list[MemorySource] = []
    for source in pending:
        candidate = [*batch, source]
        if counter.count_text(_project(candidate, (), (), {}, excluded).text) > _NEW_MESSAGES_TOKENS:
            if not batch:
                # Leave the original in Redis; a bounded excerpt prevents a single outlier stalling a chat.
                excerpt = source.text[:600] + "\n[message clipped]\n" + source.text[-600:]
                batch.append(source.model_copy(update={"text": excerpt}))
            break
        batch.append(source)

    heads = _relevant_heads(state, batch)
    support = _supporting_sources(state, batch, heads)
    authors = {source.speaker_tid for source in [*batch, *support]}
    authored_text = "\n".join(source.text.casefold() for source in batch)
    names: dict[int, set[str]] = defaultdict(set)
    for source in state.sources:
        if source.speaker_tid in excluded:
            continue
        for name in (source.speaker_name, f"@{source.username}" if source.username else ""):
            if not name or len(names[source.speaker_tid]) >= 3:
                continue
            if source.speaker_tid in authors or re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", authored_text):
                names[source.speaker_tid].add(name[:80])
    names = {identity: aliases for identity, aliases in names.items() if aliases}
    prompt = _project(batch, support, heads, names, excluded)
    parents = {source.reply_to_message_id for source in batch}
    while counter.count_text(prompt.text) > WRITER_DATA_TOKENS:
        if support and support[-1].message_id not in parents:
            support.pop()
        elif heads:
            heads.pop()
        elif support:
            support.pop()
        elif names:
            names.popitem()
        else:
            raise ValueError("Memory writer data exceeds its token budget")
        prompt = _project(batch, support, heads, names, excluded)
    return prompt


def partition_routine(
    prompt: WriterPrompt, output: RoutineMemoryOutput, facts: Sequence[MemoryFact]
) -> tuple[list[MemoryAction], set[int]]:
    review = prompt.decode_review(output.review)
    messages = {source.message_id: source for source in prompt.messages.values()}
    actions = prompt.decode_actions(output.actions)
    superseded = {fact.previous_id for fact in facts}
    for action in actions:
        topics = {messages[identity].topic_id for identity in action.evidence_ids}
        matches_head = any(
            head.speaker_tid == action.speaker_tid
            and head.topic_id in topics
            and head.event.casefold() == action.event.casefold()
            and head.fact_id not in superseded
            for head in facts
        )
        if (
            action.action != "ADD"
            or action.layer != "personal"
            or action.kind not in {"fact", "preference"}
            or action.scope != "person"
            or action.target_id is not None
            or matches_head
            or action.subject_tids != [action.speaker_tid]
            or any(messages[identity].speaker_tid != action.speaker_tid for identity in action.evidence_ids)
        ):
            review.update(prompt.batch_ids.intersection(action.evidence_ids))

    # Review shared evidence together, even if the routine stage split it into multiple actions.
    while True:
        expanded = review.union(
            identity
            for action in actions
            if review.intersection(action.evidence_ids)
            for identity in action.evidence_ids
            if identity in prompt.batch_ids
        )
        if expanded == review:
            break
        review = expanded
    return [action for action in actions if not review.intersection(action.evidence_ids)], review


async def extract_memories(
    state: MemoryState,
    pending: Sequence[MemorySource],
    counter: TokenCounter,
    *,
    routine: Callable[[WriterPrompt], Awaitable[RoutineMemoryOutput | None]] | None,
    review: Callable[[WriterPrompt], Awaitable[WriterOutput | None]],
) -> tuple[MemoryActions, set[int]]:
    prompt = build_writer_prompt(state, pending, counter)
    if not prompt.batch_ids:
        return MemoryActions(), set()
    actions: list[MemoryAction] = []
    review_ids = set(prompt.batch_ids)
    if routine is not None:
        if (result := await routine(prompt)) is None:
            return MemoryActions(), set()
        actions, review_ids = partition_routine(prompt, result, state.facts)
    completed = set(prompt.batch_ids) - review_ids
    if review_ids:
        review_prompt = (
            prompt
            if review_ids == prompt.batch_ids
            else build_writer_prompt(state, [source for source in pending if source.message_id in review_ids], counter)
        )
        if (reviewed := await review(review_prompt)) is not None:
            actions.extend(review_prompt.decode_actions(reviewed.actions))
            completed.update(review_prompt.batch_ids)
    return MemoryActions(actions=actions), completed
