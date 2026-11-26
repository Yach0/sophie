from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from aiogram import F, Router
from aiogram.types import Message
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType
from mistralai.client.errors import SDKError

from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, ai_error_boundary
from sophie_bot.modules.error.handlers.error import SophieErrorHandler
from tests.e2e.helpers import create_test_user_and_group


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("unexpected", [False, True])
async def test_origin_controls_delivery_without_losing_operator_report(
    test_client: TestClient,
    extra_router: Callable[[Router], Router],
    runtime_sentry_events: list[dict[str, Any]],
    automatic: bool,
    unexpected: bool,
) -> None:
    member, group, _model = await create_test_user_and_group(test_client)
    failure = (
        RuntimeError("private programming details")
        if unexpected
        else SDKError('{"code":"2314","message":"billing_no_api_budget_configured"}', httpx.Response(402))
    )
    router = Router(name="ai_error_presentation")

    @router.message(F.text == "AI_FAILURE_PRESENTATION")
    @ai_error_boundary(AIErrorContext(operation="translation", origin="automatic" if automatic else "explicit"))
    async def failing_action(_message: Message) -> None:
        raise failure

    extra_router(router)
    requests = await test_client.send_message(text="AI_FAILURE_PRESENTATION", from_user=member, chat=group)
    (event,) = runtime_sentry_events
    assert event["tags"]["ai.origin"] == ("automatic" if automatic else "explicit")
    assert event["tags"]["ai.operation"] == "translation"
    assert event["contexts"]["ai_request"]["origin"] == ("automatic" if automatic else "explicit")
    reference = event["event_id"]
    assert SophieErrorHandler.capture_sentry(failure) == reference
    assert len(runtime_sentry_events) == 1
    texts = [request.params["text"] for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    if automatic:
        assert not texts
    else:
        assert len(texts) == 1
        assert "Translation could not be completed" in texts[0]
        assert "try again" in texts[0]
        assert reference in texts[0]
        assert "2314" not in texts[0]
        assert "billing" not in texts[0]
        assert "private" not in texts[0]
        assert "<blockquote>" not in texts[0]
