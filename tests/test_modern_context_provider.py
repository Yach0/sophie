from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from sophie_bot.db.models.ai.ai_catalog import AIProviderKind
from sophie_bot.modules.ai.utils import ai_catalog, ai_run
from sophie_bot.modules.ai.utils.ai_catalog import AICatalog, CatalogModel, CatalogProvider
from sophie_bot.modules.ai.utils.ai_model_factory import pinned_candidate, registered_context_window_tokens
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions, _candidate_run_kwargs

SESSION_ID = "8df4ba52-4f4c-4e48-90d7-6e1e93246a92"


def _completion(model_name: str) -> dict[str, Any]:
    return {
        "id": "completion-test",
        "object": "chat.completion",
        "created": 0,
        "model": model_name,
        "provider": "Anthropic" if model_name.startswith("anthropic/") else "OpenAI",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 2,
            "total_tokens": 22,
            "prompt_tokens_details": {"cached_tokens": 12, "cache_write_tokens": 4},
        },
    }


@pytest.mark.parametrize(
    ("provider_kind", "model_name", "base_url", "explicit_cache"),
    [
        ("openrouter", "anthropic/claude-sonnet-4.5", "https://openrouter.ai/api/v1", True),
        ("openrouter", "openai/gpt-5", "https://openrouter.ai/api/v1", False),
        ("openai", "gpt-5", "https://api.openai.com/v1", False),
        ("openai", "custom/model", "https://compatible.example/v1", False),
    ],
)
async def test_modern_cache_controls_reach_supported_wire_payloads(
    provider_kind: str, model_name: str, base_url: str, explicit_cache: bool
) -> None:
    payloads: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        return httpx2.Response(200, json=_completion(model_name))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        if provider_kind == "openrouter":
            model = OpenRouterModel(
                model_name,
                provider=OpenRouterProvider(api_key="offline", http_client=client),
            )
        else:
            model = OpenAIChatModel(
                model_name,
                provider=OpenAIProvider(api_key="offline", base_url=base_url, http_client=client),
            )

        def lookup() -> str:
            """Look up an aliased message."""
            return "message_1"

        agent = Agent(model, instructions="Stable alias-only instructions.", tools=[lookup])
        kwargs = _candidate_run_kwargs(
            {"user_prompt": "speaker_1: hello"},
            None,
            AIRequestOptions(
                user_tracking_id=-1001234567890, session_id=SESSION_ID, service_tier="flex", prompt_cache=True
            ),
            AIModelCandidate(model=model, model_name=model_name, service_tier="none"),
            model,
        )
        result = await agent.run(**kwargs)

    payload = payloads[0]
    encoded = json.dumps(payload)
    assert "-1001234567890" not in encoded
    assert "user" not in payload
    assert "service_tier" not in payload
    assert ("cache_control" in encoded) is explicit_cache
    if provider_kind == "openrouter":
        assert payload["session_id"] == SESSION_ID
        assert "prompt_cache_key" not in payload
        assert result.usage.cache_write_tokens == 4
        if explicit_cache:
            assert payload["messages"][0]["content"][-1]["cache_control"]["type"] == "ephemeral"
            assert payload["messages"][-1]["content"][-1]["cache_control"]["type"] == "ephemeral"
            assert payload["tools"][-1]["cache_control"]["type"] == "ephemeral"
    else:
        assert "session_id" not in payload
        if base_url == "https://api.openai.com/v1":
            assert payload["prompt_cache_key"] == SESSION_ID
        else:
            assert "prompt_cache_key" not in payload
    assert "prompt_cache_retention" not in payload
    assert result.usage.cache_read_tokens == 12


def _registered_capacities(monkeypatch: pytest.MonkeyPatch, capacities: dict[str, int | None]) -> None:
    provider = CatalogProvider(
        name="openrouter", kind=AIProviderKind.openrouter, base_url=None, api_key="offline"
    )
    monkeypatch.setattr(
        ai_catalog,
        "_catalog",
        AICatalog(
            version="capacity-tests",
            providers={provider.name: provider},
            models={
                name: CatalogModel(
                    name=name,
                    provider=provider,
                    api_name=name,
                    supports_reasoning=True,
                    extra_params=None,
                    context_window_tokens=capacity,
                )
                for name, capacity in capacities.items()
            },
        ),
    )


def test_plan_uses_only_explicit_registry_capacities() -> None:
    candidates = (
        AIModelCandidate(
            model=TestModel(profile={"context_window": 1024}),
            model_name="wide",
            context_window_tokens=32768,
        ),
        AIModelCandidate(
            model=TestModel(profile={"context_window": 131072}),
            model_name="narrow",
            context_window_tokens=8192,
        ),
    )
    assert AIModelPlan(candidates=candidates).context_window_tokens == 8192


@pytest.mark.parametrize("capacity", [None, 0, -1])
def test_plan_rejects_missing_or_invalid_registry_size_even_with_provider_profile(capacity: int | None) -> None:
    model = TestModel(profile={"context_window": 32768})
    candidate = AIModelCandidate(model=model, model_name="unconfigured/model", context_window_tokens=capacity)
    with pytest.raises(ValueError, match="unconfigured/model.*context_window_tokens"):
        _ = AIModelPlan(candidates=(candidate,)).context_window_tokens


def test_empty_plan_cannot_supply_a_context_size() -> None:
    with pytest.raises(ValueError, match="no candidates"):
        _ = AIModelPlan().context_window_tokens


def test_runtime_capacity_includes_registered_last_resort(monkeypatch: pytest.MonkeyPatch) -> None:
    _registered_capacities(monkeypatch, {ai_run.AI_FALLBACK_MODEL_NAME: 4096})
    primary = TestModel(model_name="primary")
    fallback = TestModel(model_name="fallback", profile={"context_window": 65536})
    monkeypatch.setattr(ai_run, "get_ai_model", lambda _: fallback)
    plan = AIModelPlan(
        candidates=(AIModelCandidate(model=primary, model_name="primary", context_window_tokens=32768),)
    )
    assert ai_run.modern_context_window_tokens(plan) == 4096


@pytest.mark.parametrize("register_fallback", [False, True])
def test_runtime_rejects_last_resort_without_registered_size(
    monkeypatch: pytest.MonkeyPatch, register_fallback: bool
) -> None:
    _registered_capacities(monkeypatch, {ai_run.AI_FALLBACK_MODEL_NAME: None} if register_fallback else {})
    primary = TestModel(model_name="primary")
    fallback = TestModel(model_name="fallback", profile={"context_window": 32768})
    monkeypatch.setattr(ai_run, "get_ai_model", lambda _: fallback)
    plan = AIModelPlan(
        candidates=(AIModelCandidate(model=primary, model_name="primary", context_window_tokens=32768),)
    )
    with pytest.raises(ValueError, match=f"{ai_run.AI_FALLBACK_MODEL_NAME}.*context_window_tokens"):
        ai_run.modern_context_window_tokens(plan)


def test_runtime_capacity_only_includes_candidates_within_attempt_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _registered_capacities(monkeypatch, {ai_run.AI_FALLBACK_MODEL_NAME: 16384})
    monkeypatch.setattr(ai_run, "get_ai_model", lambda _: TestModel(model_name="fallback"))
    plan = AIModelPlan(
        candidates=tuple(
            AIModelCandidate(model=TestModel(model_name=name), model_name=name, context_window_tokens=size)
            for name, size in (("primary", 32768), ("second", 65536), ("third", 8192), ("not-tried", None))
        )
    )
    assert ai_run.modern_context_window_tokens(plan) == 8192


def test_pinned_candidate_copies_registry_size(monkeypatch: pytest.MonkeyPatch) -> None:
    _registered_capacities(monkeypatch, {"custom/model": 49152})
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_model_factory.get_ai_model",
        lambda _: TestModel(model_name="custom/model", profile={"context_window": 1024}),
    )
    assert pinned_candidate("custom/model").context_window_tokens == 49152


def test_unregistered_pinned_model_stays_legacy_usable_but_cannot_supply_modern_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registered_capacities(monkeypatch, {})
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_model_factory.get_ai_model",
        lambda _: TestModel(model_name="custom/model", profile={"context_window": 32768}),
    )
    candidate = pinned_candidate("custom/model")
    assert candidate.context_window_tokens is None
    assert AIModelPlan(candidates=(candidate,)).primary is candidate.model
    with pytest.raises(ValueError, match="custom/model.*context_window_tokens"):
        _ = AIModelPlan(candidates=(candidate,)).context_window_tokens


def test_unregistered_agent_model_receives_no_profile_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    _registered_capacities(monkeypatch, {})
    model = TestModel(model_name="manual/model", profile={"context_window": 32768})
    candidate = ai_run._agent_candidate(model, None)
    assert candidate.context_window_tokens is None
    with pytest.raises(ValueError, match="manual/model.*context_window_tokens"):
        candidate.require_context_window_tokens()


def test_registered_capacity_lookup_accepts_exact_upstream_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = CatalogProvider(
        name="custom", kind=AIProviderKind.openai_compatible, base_url=None, api_key="offline"
    )
    monkeypatch.setattr(
        ai_catalog,
        "_catalog",
        AICatalog(
            models={
                "custom/model": CatalogModel(
                    name="custom/model",
                    provider=provider,
                    api_name="upstream-model",
                    supports_reasoning=False,
                    extra_params=None,
                    context_window_tokens=65536,
                )
            }
        ),
    )
    assert registered_context_window_tokens("custom/model") == 65536
    assert registered_context_window_tokens("upstream-model") == 65536
    assert registered_context_window_tokens("other/upstream-model") is None


@pytest.mark.parametrize("missing_name", ["primary", "backup"])
def test_runtime_rejects_unconfigured_primary_or_backup(
    monkeypatch: pytest.MonkeyPatch, missing_name: str
) -> None:
    _registered_capacities(monkeypatch, {ai_run.AI_FALLBACK_MODEL_NAME: 32768})
    monkeypatch.setattr(ai_run, "get_ai_model", lambda _: TestModel(model_name="fallback"))
    plan = AIModelPlan(
        candidates=tuple(
            AIModelCandidate(
                model=TestModel(model_name=name, profile={"context_window": 65536}),
                model_name=name,
                context_window_tokens=None if name == missing_name else 32768,
            )
            for name in ("primary", "backup")
        )
    )
    with pytest.raises(ValueError, match=f"{missing_name}.*context_window_tokens"):
        ai_run.modern_context_window_tokens(plan)


def test_ambiguous_upstream_alias_does_not_choose_a_registry_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CatalogProvider(
        name="custom", kind=AIProviderKind.openai_compatible, base_url=None, api_key="offline"
    )
    monkeypatch.setattr(
        ai_catalog,
        "_catalog",
        AICatalog(
            models={
                name: CatalogModel(
                    name=name,
                    provider=provider,
                    api_name="shared-upstream-name",
                    supports_reasoning=False,
                    extra_params=None,
                    context_window_tokens=capacity,
                )
                for name, capacity in (("first/model", 32768), ("second/model", 65536))
            }
        ),
    )
    assert registered_context_window_tokens("shared-upstream-name") is None
