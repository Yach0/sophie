"""Operator-only attachment serialization, using existing context and prepared requests."""

from __future__ import annotations

import json
import re

from aiogram.types import BufferedInputFile
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.usage import RunUsage, UsageLimits

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.ai_catalog import catalog
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_tokens import TOOL_SCHEMA_TOKEN_RESERVE
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.ai.utils.old_context import CHATBOT_CACHE_MESSAGE_LIMIT, OldContext
from sophie_bot.utils.feature_flags import FeatureType, get_value

_REQUEST_PARAMETERS = TypeAdapter(ModelRequestParameters)
_RUN_USAGE = TypeAdapter(RunUsage)
_USAGE_LIMITS = TypeAdapter(UsageLimits)
_SECRET_NAME = r"(?:[a-z0-9_-]*?(?:api[_-]?key|authorization|password|passwd|secret|credentials?|token))"
_SECRET_FIELD = re.compile(_SECRET_NAME, re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    rf"""(?i)(["']?\b{_SECRET_NAME}\b["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}}\]\n]+)"""
)
_AUTH_HEADER = re.compile(r"(?im)\b(?:proxy-)?authorization\s*:\s*[^\r\n]+")
_BEARER = re.compile(r"""(?i)\b(?:bearer|basic)\s+[^\s"'<>;,}\]]+""")
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@")
_KEY_TOKEN = re.compile(r"\b(?:sk-[a-zA-Z0-9_-]+|\d{6,}:[a-zA-Z0-9_-]{20,})\b")


def _redact_plain_text(value: str, secrets: set[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = _AUTH_HEADER.sub("Authorization: [REDACTED]", value)
    value = _BEARER.sub("[REDACTED]", value)
    value = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    return _KEY_TOKEN.sub("[REDACTED]", value)


def _redact_text(value: str, secrets: set[str]) -> str:
    # Decode before inspecting keys or values: JSON escaping can hide both secret names
    # and quote boundaries. Keep encoded JSON away from the plain-text regexes.
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(decoded, (dict, list, str)):
            return json.dumps(_redact(decoded, secrets), ensure_ascii=False)

    decoder = json.JSONDecoder()
    fragments: list[str] = []
    start = 0
    position = 0
    while position < len(value):
        if value[position] not in "{[":
            position += 1
            continue
        try:
            decoded, end = decoder.raw_decode(value, position)
        except json.JSONDecodeError:
            position += 1
            continue
        fragments.append(_redact_plain_text(value[start:position], secrets))
        fragments.append(json.dumps(_redact(decoded, secrets), ensure_ascii=False))
        start = position = end
    fragments.append(_redact_plain_text(value[start:], secrets))
    return "".join(fragments)


def _redact(value: object, secrets: set[str]) -> object:
    if isinstance(value, dict):
        return {
            _redact_text(key, secrets) if isinstance(key, str) else key: "[REDACTED]"
            if _SECRET_FIELD.fullmatch(str(key).replace("-", "_"))
            or any(
                marker in str(key).lower().replace("-", "_")
                for marker in ("api_key", "password", "secret", "credential", "headers", "cookie")
            )
            else _redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


async def build_chatbot_debug_file(
    history: OldContext | ModernContext,
    result: AIAgentResult[str] | None,
    model_plan: AIModelPlan,
    context: SophieAIToolContext,
    *,
    service_tier: str | None,
) -> BufferedInputFile:
    """Never serialize models/clients/config wholesale: select diagnostic data explicitly."""
    model = (result.served_model or model_plan.primary) if result is not None else None
    request = result.last_model_request if result is not None else None
    secrets = {
        value
        for name, value in CONFIG.model_dump().items()
        if _SECRET_FIELD.search(name) and isinstance(value, str) and value
    }
    secrets.update(provider.api_key for provider in catalog().providers.values() if provider.api_key)
    for candidate_model in [model, *(candidate.model for candidate in model_plan.candidates)]:
        if isinstance(candidate_model, (OpenAIChatModel, OpenAIResponsesModel, OpenRouterModel)):
            secrets.add(candidate_model.client.api_key)
    secrets.discard("")
    # These flags are already consumed by chatbot preparation/execution. Do not inspect unrelated
    # flags, credentials, environment variables, or trigger additional tool/instruction builders.
    flag_names: tuple[FeatureType, ...] = (
        "ai_chatbot",
        "ai_chatbot_modern_context",
        "ai_chatbot_request_limit",
        "ai_chatbot_tool_calls_limit",
        "ai_chatbot_response_tokens_limit",
        "ai_search_provider",
    )
    flag_names += (
        ("ai_chatbot_modern_context_tokens",)
        if isinstance(history, ModernContext)
        else ("ai_chatbot_history_max_age_minutes",)
    )
    flags = {
        name: await get_value(name, chat_tid=context.chat_tid, redis=context.services.redis) for name in flag_names
    }
    payload = {
        "mode": context.mode.value,
        "execution_status": "completed" if result is not None else "unavailable",
        "execution_note": "Effective execution data and prepared request unavailable after AIRequestFailed; "
        "model_plan and service_tier_default describe configured defaults only. Built context is retained."
        if result is None
        else None,
        "model": model.model_name if model is not None else None,
        "provider": model.system if model is not None else None,
        "provider_base_url": model.base_url if model is not None else None,
        "model_settings": ({**(model.settings or {}), **(request.model_settings or {})} if request else model.settings)
        if model is not None
        else None,
        "service_tier_default": service_tier,
        "flags": flags,
        "usage_limits": _USAGE_LIMITS.dump_python(result.request_usage_limits, mode="json")
        if result is not None and result.request_usage_limits is not None
        else None,
        "usage": _RUN_USAGE.dump_python(result.usage, mode="json") if result is not None else None,
        "retries": result.retries if result is not None else None,
        "model_plan": [
            {
                "model": candidate.model_name,
                "provider": candidate.model.system,
                "provider_base_url": candidate.model.base_url,
                "context_window_tokens": candidate.context_window_tokens,
                "supports_images": candidate.supports_images,
                "service_tier": candidate.resolve_service_tier(service_tier),
                "settings": candidate.model.settings,
            }
            for candidate in model_plan.candidates
        ],
        "context_budget": history.debug_budget()
        if isinstance(history, ModernContext)
        else {
            "cache_message_limit": CHATBOT_CACHE_MESSAGE_LIMIT,
            "trimming": "Legacy cache/history limits applied before this snapshot; removed content is not retained.",
        },
        "tool_schema_token_reserve": TOOL_SCHEMA_TOKEN_RESERVE if isinstance(history, ModernContext) else None,
        "built_history": ModelMessagesTypeAdapter.dump_python(history.message_history, mode="json"),
        "built_instructions": history.instructions if isinstance(history, ModernContext) else None,
        "runtime_context": history.runtime_context if isinstance(history, ModernContext) else None,
        "current_prompt": ModelMessagesTypeAdapter.dump_python(
            [ModelRequest(parts=[UserPromptPart(content=history.prompt)])], mode="json"
        ),
        "last_prepared_request": {
            "messages": ModelMessagesTypeAdapter.dump_python(request.messages, mode="json"),
            "parameters": _REQUEST_PARAMETERS.dump_python(request.model_request_parameters, mode="json"),
            "streaming": request.streaming,
            "note": "Final prepared model request, after candidate context projection and tool continuations. "
            "Earlier requests are represented by result_history; earlier per-attempt settings are not retained.",
        }
        if request is not None
        else None,
        "result_history": ModelMessagesTypeAdapter.dump_python(result.message_history, mode="json")
        if result is not None
        else None,
    }
    # Redact strings before JSON encoding, including tool argument JSON embedded in strings.
    text = json.dumps(_redact(payload, secrets), ensure_ascii=False, indent=2)
    return BufferedInputFile(text.encode("utf-8"), filename="llm_debug.txt")
