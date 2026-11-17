from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, Update, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.types import RequestType
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from sophie_bot.db.models import ChatModel, NoteModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.modules.ai.handlers import ai_cmd
from sophie_bot.modules.ai.utils import support_replies
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.support_replies import SupportDecision
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_message_id, next_user_id, set_feature
from tests.e2e.test_ai_commands_e2e import _simulate_rich_send_response


@pytest.mark.parametrize(
    "scenario",
    (
        "disabled",
        "answer",
        "ask_for_details",
        "ungrounded",
        "admin",
        "channel",
        "bot",
        "staff_during_grace",
        "staff_takes_over",
        "explicit_during_grace",
        "explicit_during_draft",
        "duplicate",
        "simultaneous_duplicates",
        "paraphrased_duplicate",
    ),
)
async def test_proactive_support(
    test_client: TestClient,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    member, group, _user_model = await create_test_user_and_group(test_client)
    admin = User(id=next_user_id(), first_name="Admin", is_bot=False)
    await grant_admin(group.id, admin.id, creator=True)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.support)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_proactive_support", scenario != "disabled", chat_tid=group.id)

    note_text = (
        "If the beta form fails, ask the member for the error message."
        if scenario == "ask_for_details"
        else "Use the beta form to join."
    )
    await NoteModel(chat_id=group.id, chat=chat, names=("beta",), text=note_text, embedding=[1.0]).insert()
    embed = AsyncMock(return_value=[1.0])
    monkeypatch.setattr("sophie_bot.utils.notes_search.create_embedding", embed)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))

    async def feed(
        sender: User, text: str, *, parent: Message | None = None, sender_chat: Chat | None = None
    ) -> Message:
        message = MessageFactory.create(
            text=text,
            from_user=sender,
            chat=group,
            message_id=next_message_id(),
            date=datetime.now(UTC).replace(microsecond=0),
            reply_to_message=parent,
        ).model_copy(update={"sender_chat": sender_chat})
        await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=message))
        return message

    grace_ended = asyncio.Event()

    async def wait_for_grace(seconds: int) -> None:
        assert seconds == 16
        await grace_ended.wait()

    monkeypatch.setattr(support_replies, "sleep", wait_for_grace)
    attempts = 0
    explicit_started = asyncio.Event()
    explicit_finished = asyncio.Event()
    explicit_task: asyncio.Task[Message] | None = None

    async def wait_for_explicit_reply(*args: object, **kwargs: object) -> None:
        explicit_started.set()
        await explicit_finished.wait()

    explicit_reply = AsyncMock(side_effect=wait_for_explicit_reply)
    monkeypatch.setattr(ai_cmd, "ai_chatbot_reply", explicit_reply)
    skips: list[str] = []
    monkeypatch.setattr(support_replies, "_skip", skips.append)

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal attempts, explicit_task
        attempts += 1
        assert not info.function_tools
        if scenario == "staff_takes_over":
            await feed(admin, "I'll check this", parent=question)
        if scenario == "explicit_during_draft":
            explicit_task = asyncio.create_task(feed(member, "/ai", parent=question))
            await explicit_started.wait()
        if scenario == "paraphrased_duplicate" and attempts == 2:
            assert '"role": "Sophie"' in str(messages)
            decision = SupportDecision(abstain_reason="already_answered")
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decision.model_dump())])
        excerpt = "Made-up evidence" if scenario == "ungrounded" else note_text
        answer = (
            "What error message did the beta form show?"
            if scenario == "ask_for_details"
            else "Use the beta form to join."
        )
        decision = SupportDecision(
            answer=answer, note_name="beta", excerpt=excerpt, requests_details=scenario == "ask_for_details"
        )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decision.model_dump())])

    model = FunctionModel(respond, model_name="offline")
    plan = AIModelPlan(candidates=(AIModelCandidate(model, "offline"),))
    monkeypatch.setattr(support_replies, "get_proactive_replies_model_plan", AsyncMock(return_value=plan))

    sender = admin if scenario == "admin" else member
    if scenario == "bot":
        sender = User(id=next_user_id(), first_name="Other bot", is_bot=True)
    start = len(test_client.capture.all_requests)
    with ExitStack() as stack:
        _simulate_rich_send_response(test_client, stack)
        question = await feed(
            sender,
            "The beta form failed" if scenario == "ask_for_details" else "I still can't get into the beta",
            sender_chat=group if scenario == "channel" else None,
        )
        if scenario == "staff_during_grace":
            await feed(admin, "I'll check this", parent=question)
        if scenario == "explicit_during_grace":
            explicit_task = asyncio.create_task(feed(member, "/ai", parent=question))
            await explicit_started.wait()
        if scenario == "simultaneous_duplicates":
            other = User(id=next_user_id(), first_name="Other member", is_bot=False)
            await feed(other, "I still can't get into the beta")
        grace_ended.set()
        await asyncio.gather(*tuple(test_services.background_tasks))
        explicit_finished.set()
        if explicit_task:
            await explicit_task
        if scenario in {"duplicate", "paraphrased_duplicate"}:
            other = User(id=next_user_id(), first_name="Other member", is_bot=False)
            await feed(
                other,
                "Can someone tell me how to join the beta?"
                if scenario == "paraphrased_duplicate"
                else "I still can't get into the beta",
            )
            await asyncio.gather(*tuple(test_services.background_tasks))
            assert "already_answered" in skips

    requests = test_client.capture.all_requests[start:]
    sends = [request for request in requests if request.request_type == RequestType.OTHER]
    if scenario in {"answer", "ask_for_details", "duplicate", "simultaneous_duplicates", "paraphrased_duplicate"}:
        if scenario == "simultaneous_duplicates":
            assert attempts in {1, 2}, skips
        else:
            assert attempts == (2 if scenario == "paraphrased_duplicate" else 1), skips
        assert len(sends) == 1
        assert "Source note: beta" in str(sends[0].params)
        if scenario == "ask_for_details":
            assert "What error message did the beta form show?" in str(sends[0].params)
        if scenario != "simultaneous_duplicates":
            assert str(question.message_id) in str(sends[0].params["reply_parameters"])
    else:
        assert not sends
        if scenario == "staff_takes_over":
            assert attempts == 1
            assert "staff_replied" in skips
        elif scenario.startswith("explicit_during_"):
            explicit_reply.assert_awaited_once()
            assert attempts == int(scenario == "explicit_during_draft")
            assert "explicit_request" in skips
        elif scenario == "ungrounded":
            assert attempts == 1
            assert skips == ["insufficient_evidence"]
        else:
            assert attempts == 0
            if scenario == "staff_during_grace":
                assert "staff_replied" in skips
                embed.assert_not_awaited()
