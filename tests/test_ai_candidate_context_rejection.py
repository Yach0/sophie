from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions
from sophie_bot.modules.ai.utils.ai_tokens import ModelTokenBudget, load_token_counter
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("rejected_name", ["unknown", "glm-5.3"])
@pytest.mark.parametrize("later_accepts", [False, True])
async def test_candidate_budget_rejection_preserves_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
    rejected_name: str,
    later_accepts: bool,
) -> None:
    requested_models: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        if len(requested_models) == 1:
            return httpx2.Response(
                400, json={"error": {"message": "try another model", "type": "invalid_request_error"}}
            )
        assert payload["model"] == "glm-5.3-flash"
        assert later_accepts
        assert history.prompt[0] in json.dumps(payload)
        if streaming:
            chunk = {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": payload["model"],
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello"}, "finish_reason": None}],
            }
            finished = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(finished) + "\n\ndata: [DONE]\n\n",
            )
        return httpx2.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                ],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = OpenAIProvider(api_key="offline", http_client=client)
        primary = OpenAIChatModel("glm-5.3", provider=provider)
        rejected = OpenAIChatModel(rejected_name, provider=provider)
        later = OpenAIChatModel("glm-5.3-flash", provider=provider)
        monkeypatch.setattr(ai_run, "get_ai_model", lambda name: later)
        candidates = (
            AIModelCandidate(model=primary, model_name="primary", context_window_tokens=200000),
            AIModelCandidate(
                model=rejected,
                model_name="rejected",
                context_window_tokens=200000 if rejected_name == "unknown" else 7000,
            ),
            AIModelCandidate(model=later, model_name="later", context_window_tokens=200000 if later_accepts else 7000),
        )
        history = ModernContext(redis=MagicMock(), chat_tid=1)
        # deepsource: skip - PYL-R1728 - async comprehension with await cannot be a generator for tuple()
        history._token_budgets = tuple(
            [
                ModelTokenBudget.for_model(
                    await load_token_counter(candidate.model.model_name),
                    candidate.require_context_window_tokens(),
                    0,
                    2048,
                )
                for candidate in candidates
            ]
        )
        history._budget = history._token_budgets[0].input_tokens
        history.prompt = ["current request " * 2000]
        before = history._state.model_dump_json()
        prompt_before = list(history.prompt)
        agent = Agent(primary)
        options = AIRequestOptions(context_projection=history.project_for_candidate)
        plan = AIModelPlan(candidates)
        updates: list[str] = []

        async def on_text(text: str) -> None:
            updates.append(text)

        async def run() -> ai_run.AIAgentResult[str]:
            if streaming:
                return await ai_run.run_ai_stream(
                    agent, history.prompt, on_text, request_options=options, model_plan=plan
                )
            return await ai_run.run_ai_text(agent, history.prompt, request_options=options, model_plan=plan)

        if later_accepts:
            result = await run()
            assert result.output == "hello"
            assert result.served_model is later
            assert requested_models == ["glm-5.3", "glm-5.3-flash"]
            if streaming:
                assert updates[-1] == "hello"
        else:
            with pytest.raises(AIRequestFailed) as failure:
                await run()
            assert isinstance(failure.value.__cause__, ValueError)
            assert "exceeds its token budget" in str(failure.value.__cause__)
            assert requested_models == ["glm-5.3"]
            assert updates == []
        assert history._state.model_dump_json() == before
        assert history.prompt == prompt_before
