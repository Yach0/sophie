"""Compare the tiered writer with single-model writers on labeled synthetic or approved data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

from sophie_bot.db.models.ai.ai_evidence_memory import MemoryFact, MemoryKind, MemoryLayer, MemoryState
from sophie_bot.modules.ai.schedules.write_memory import (
    COMPACT_INSTRUCTIONS,
    ROUTINE_INSTRUCTIONS,
    WRITER_INSTRUCTIONS,
    _scope_instructions,
)
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions, run_ai_structured
from sophie_bot.modules.ai.utils.ai_tokens import TokenCounter, load_token_counter
from sophie_bot.modules.ai.utils.evidence_memory import apply_actions
from sophie_bot.modules.ai.utils.memory_writer import (
    RoutineMemoryOutput,
    WriterOutput,
    WriterPrompt,
    WriterScopes,
    extract_memories,
)


class ExpectedMemory(BaseModel):
    speaker_tids: set[int]
    subject_tids: set[int] = Field(default_factory=set)
    optional_subject_tids: set[int] = Field(default_factory=set)
    evidence_ids: set[int]
    kind: MemoryKind
    layer: MemoryLayer
    previous_id: str | None = None
    text_contains: list[str] = Field(default_factory=list)

    def matches(self, fact: MemoryFact) -> bool:
        return (
            fact.speaker_tid in self.speaker_tids
            and self.subject_tids <= set(fact.subject_tids) <= self.subject_tids | self.optional_subject_tids
            and self.evidence_ids <= set(fact.evidence_ids)
            and fact.kind == self.kind
            and fact.layer == self.layer
            and fact.previous_id == self.previous_id
            and all(text.casefold() in fact.text.casefold() for text in self.text_contains)
        )


class ReplayCase(BaseModel):
    name: str
    state: MemoryState
    pending: list[int]
    expected: list[ExpectedMemory]

    @model_validator(mode="after")
    def validate_references(self) -> ReplayCase:
        pending = set(self.pending)
        sources = {source.message_id: source for source in self.state.sources}
        if (
            not pending
            or len(pending) != len(self.pending)
            or len(sources) != len(self.state.sources)
            or not pending <= sources.keys()
            or any(sources[identity].processed for identity in pending)
        ):
            raise ValueError("A replay case must have distinct pending sources")
        if any(
            not label.evidence_ids <= sources.keys() or not label.evidence_ids.intersection(pending)
            for label in self.expected
        ):
            raise ValueError("Expected memories must reference evidence in the case and the pending batch")
        return self


class ReplayScore(BaseModel):
    cases: int = 0
    failures: int = 0
    correct: int = 0
    missed: int = 0
    extra: int = 0
    responses: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    responses_with_cost: int = 0
    reported_cost_usd: float = 0.0
    case_results: list[ReplayCaseResult] = Field(default_factory=list)

    def count_facts(self, expected: Sequence[ExpectedMemory], actual: Sequence[MemoryFact]) -> None:
        unmatched = list(actual)
        for label in expected:
            match = next((fact for fact in unmatched if label.matches(fact)), None)
            if match is None:
                self.missed += 1
            else:
                unmatched.remove(match)
                self.correct += 1
        self.extra += len(unmatched)

    def record_case(self, case: ReplayCase, facts: Sequence[MemoryFact], *, failure: str | None = None) -> None:
        before = (self.correct, self.missed, self.extra)
        self.count_facts(case.expected, facts)
        self.failures += int(failure is not None)
        self.case_results.append(
            ReplayCaseResult(
                name=case.name,
                correct=self.correct - before[0],
                missed=self.missed - before[1],
                extra=self.extra - before[2],
                failure=failure,
            )
        )

    def summary(self) -> dict[str, object]:
        complete_cost = self.responses == self.responses_with_cost
        return {
            **self.model_dump(),
            "reported_cost_per_correct_usd": (
                self.reported_cost_usd / self.correct if self.correct and complete_cost else None
            ),
        }


class ReplayCaseResult(BaseModel):
    name: str
    correct: int
    missed: int
    extra: int
    failure: str | None = None


async def score_case(
    case: ReplayCase,
    reviewer: OpenRouterModel,
    routine_model: OpenRouterModel | None,
    counter: TokenCounter,
    score: ReplayScore,
) -> None:
    scopes = WriterScopes(personal=True, group=True, updates=True, shared=True)

    async def observe(
        _ctx: RunContext[None], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        score.responses += 1
        score.input_tokens += response.usage.input_tokens
        score.output_tokens += response.usage.output_tokens
        cost = (response.provider_details or {}).get("cost")
        if isinstance(cost, int | float):
            score.responses_with_cost += 1
            score.reported_cost_usd += cost
        return response

    async def run[OutputT: BaseModel](
        model: OpenRouterModel, prompt: WriterPrompt, output_type: type[OutputT], *, is_routine: bool
    ) -> OutputT:
        system = [WRITER_INSTRUCTIONS, _scope_instructions(scopes), COMPACT_INSTRUCTIONS]
        if is_routine:
            system.append(ROUTINE_INSTRUCTIONS)
        agent = Agent(model, output_type=output_type)
        result = await run_ai_structured(
            agent,
            prompt.text,
            [ModelRequest(parts=[SystemPromptPart(text) for text in system])],
            request_options=AIRequestOptions(single_model=True),
            usage_limits=UsageLimits(request_limit=2, output_tokens_limit=3_000),
            capabilities=[Hooks(after_model_request=observe)],
        )
        return result.output

    async def routine(prompt: WriterPrompt) -> RoutineMemoryOutput:
        assert routine_model is not None
        return await run(routine_model, prompt, RoutineMemoryOutput, is_routine=True)

    async def review(prompt: WriterPrompt) -> WriterOutput:
        return await run(reviewer, prompt, WriterOutput, is_routine=False)

    score.cases += 1
    state = case.state.model_copy(deep=True)
    previous_ids = {fact.fact_id for fact in state.facts}
    pending = [source for source in state.sources if source.message_id in case.pending]
    try:
        output, completed = await extract_memories(
            state, pending, counter, routine=routine if routine_model is not None else None, review=review
        )
        if completed != set(case.pending) or not apply_actions(
            state, output, completed, state.generation, datetime.now(UTC)
        ):
            raise ValueError("Incomplete replay")
    except AIRequestFailed as error:
        score.record_case(case, [], failure=type(error.__cause__ or error).__name__)
        return
    except ValueError as error:
        failure = str(error) if type(error) is ValueError else type(error).__name__
        score.record_case(case, [], failure=failure)
        return
    score.record_case(case, [fact for fact in state.facts if fact.fact_id not in previous_ids])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("ai_memory_replay_cases.json"))
    parser.add_argument("--review-model", default="openai/gpt-6.1-terra")
    parser.add_argument("--routine-model", default="openai/gpt-6-luna")
    parser.add_argument("--against", help="Optional third standalone candidate, for example z-ai/glm-5.3-flash")
    parser.add_argument("--run", action="store_true", help="Send the labeled chats to OpenRouter and incur API charges")
    args = parser.parse_args()
    cases = [ReplayCase.model_validate(case) for case in json.loads(args.cases.read_text())]
    if not args.run:
        print(json.dumps({"cases": [case.name for case in cases], "model_calls_enabled": False}, indent=2))
        return

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        parser.error("OPENROUTER_API_KEY is required with --run.")
    provider = OpenRouterProvider(api_key=api_key)
    terra = OpenRouterModel(
        args.review_model,
        provider=provider,
        settings={
            "openrouter_reasoning": {"effort": "low"},
            "openrouter_provider": {"require_parameters": True},
        },
    )
    luna = OpenRouterModel(args.routine_model, provider=provider, settings=terra.settings)
    variants = {"review_only": (terra, None), "routine_only": (luna, None), "tiered": (terra, luna)}
    if args.against:
        variants["challenger"] = (OpenRouterModel(args.against, provider=provider, settings=terra.settings), None)

    counter = await load_token_counter("gpt-4")
    results = {}
    for name, (reviewer, routine_model) in variants.items():
        score = ReplayScore()
        for case in cases:
            await score_case(case, reviewer, routine_model, counter, score)
        results[name] = score.summary()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
