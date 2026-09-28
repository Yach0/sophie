from __future__ import annotations

from typing import Any

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import ApproveChatJoinRequest, DeclineChatJoinRequest, TelegramMethod
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType

from sophie_bot.modules.error.handlers.error import SophieErrorHandler
from tests.e2e.helpers import create_test_user_and_group, grant_admin, send_join_request


def _fail_join_request_approval(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> tuple[list[ApproveChatJoinRequest], list[DeclineChatJoinRequest]]:
    approvals: list[ApproveChatJoinRequest] = []
    declines: list[DeclineChatJoinRequest] = []
    original_make_request = test_client.bot.session.make_request

    async def make_request(bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        if isinstance(method, ApproveChatJoinRequest):
            approvals.append(method)
            raise TelegramBadRequest(method=method, message=message)
        if isinstance(method, DeclineChatJoinRequest):
            declines.append(method)
        return await original_make_request(bot, method, timeout=timeout)

    monkeypatch.setattr(test_client.bot.session, "make_request", make_request)
    return approvals, declines


async def test_already_joined_request_is_completed_without_decline(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requester, group, _ = await create_test_user_and_group(test_client)
    await grant_admin(group.id, requester.id)
    approvals, declines = _fail_join_request_approval(test_client, monkeypatch, "USER_ALREADY_PARTICIPANT")

    requests = await send_join_request(test_client, group, requester)

    assert [(request.chat_id, request.user_id) for request in approvals] == [(group.id, requester.id)]
    assert not declines
    assert not any(request.request_type == RequestType.SEND_MESSAGE for request in requests)


async def test_unexpected_join_request_approval_error_is_reported(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requester, group, _ = await create_test_user_and_group(test_client)
    await grant_admin(group.id, requester.id)
    approvals, declines = _fail_join_request_approval(test_client, monkeypatch, "UNEXPECTED_APPROVAL_ERROR")
    reported: list[Exception] = []

    def record_error(error: Exception) -> None:
        reported.append(error)

    monkeypatch.setattr(SophieErrorHandler, "capture_sentry", staticmethod(record_error))


    requests = await send_join_request(test_client, group, requester)

    assert [(request.chat_id, request.user_id) for request in approvals] == [(group.id, requester.id)]
    assert not declines
    assert len(reported) == 1
    assert isinstance(reported[0], TelegramBadRequest)
    assert reported[0].message == "UNEXPECTED_APPROVAL_ERROR"
    assert reported[0].method is approvals[0]
    assert any(
        request.request_type == RequestType.SEND_MESSAGE and request.chat_id == group.id for request in requests
    )
