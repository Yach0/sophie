from __future__ import annotations

from datetime import UTC, datetime

from aiogram_test_framework import TestClient

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryModel
from sophie_bot.db.models.ai.ai_evidence_memory import (
    AIEvidenceMemoryModel,
    MemoryAction,
    MemoryActions,
    MemorySource,
    MemoryState,
)
from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.ai.utils.ai_mode import set_chat_mode
from sophie_bot.modules.ai.utils.evidence_memory import apply_actions, capture_source, mutate_memory
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import create_test_user_and_group, grant_admin, set_feature


async def test_silent_capture_controls_and_chat_boundaries(
    test_client: TestClient,
    test_services: ApplicationServices,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_feature(test_client, "ai_memory_capture", True, chat_tid=chat.tid)
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    await test_client.send_message(text="I prefer brief replies.", from_user=user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and any(source.text == "I prefer brief replies." for source in document.state.sources)
    requests = await test_client.send_command(command="ai_memory sources", from_user=user, chat=group)
    assert any("I prefer brief replies." in (request.text or "") for request in requests)
    await AIMemoryModel.append_line(chat, "Keep legacy memory")
    summary_date = datetime.now(UTC).date()
    await AIChatSummaryModel.upsert_for_date(chat, summary_date, "Keep summary", [])
    for operation in ("forget", "off"):
        requests = await test_client.send_command(command="ai_memory " + operation, from_user=user, chat=group)
        assert any("Only chat administrators" in (request.text or "") for request in requests)
        document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
        assert document and document.state.sources and not document.state.excluded_users
        assert await AIMemoryModel.get_lines(chat.iid) == ["Keep legacy memory"]
        assert await AIChatSummaryModel.get_for_date(chat.iid, summary_date) is not None
    await grant_admin(chat.tid, user.id)
    await test_client.send_command(command="ai_memory off", from_user=user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and user.id in document.state.excluded_users
    assert not document.state.sources
    assert not await AIMemoryModel.get_lines(chat.iid)
    assert await AIChatSummaryModel.get_for_date(chat.iid, summary_date) is None
    await test_client.send_message(text="Do not retain me", from_user=user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and not document.state.sources
    await test_client.send_command(command="ai_memory on", from_user=user, chat=group)
    await test_client.send_message(text="Capture resumed", from_user=user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and len(document.state.sources) == 1
    member = test_client.create_user(first_name="Member")
    await test_client.send_command(command="ai_reset", from_user=member.user, chat=group)
    # A regular member cannot reset the group's AI state.
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and document.state.sources
    await test_client.send_command(command="ai_memory forget", from_user=user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and not document.state.sources and not document.state.facts


async def test_personal_correction_ownership_and_invalid_arguments(
    test_client: TestClient,
    test_services: ApplicationServices,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    other = test_client.create_user(first_name="Other")
    await set_feature(test_client, "ai_memory_capture", True, chat_tid=chat.tid)
    await set_feature(test_client, "ai_memory_controls", True, chat_tid=chat.tid)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    now = datetime.now(UTC)

    def seed(state: MemoryState) -> None:
        capture_source(
            state, MemorySource(message_id=91919, speaker_tid=user.id, text="Friday", sent_at=now, branch_id=91919)
        )
        apply_actions(
            state,
            MemoryActions(
                actions=[
                    MemoryAction(
                        action="ADD",
                        speaker_tid=user.id,
                        subject_tids=[user.id],
                        event="picnic",
                        text="Friday",
                        evidence_ids=[91919],
                    )
                ]
            ),
            {91919},
            state.generation,
            now,
        )

    await mutate_memory(chat, seed)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document
    fact_id = document.state.facts[0].fact_id
    await test_client.send_command(command=f"ai_memory correct {fact_id} Saturday", from_user=other.user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and len(document.state.facts) == 1
    await test_client.send_command(command=f"ai_memory correct {fact_id} Saturday", from_user=user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and len(document.state.facts) == 2
    assert document.state.facts[1].text == "Saturday"
    assert document.state.facts[1].previous_id == fact_id
    requests = await test_client.send_command(command="ai_memory show", from_user=user, chat=group)
    text = "\n".join(request.text or "" for request in requests)
    assert "<b>ID</b>" in text and "<b>Text</b>" in text and "<b>Source messages</b>" in text
    assert fact_id in text and "Saturday" in text
    requests = await test_client.send_command(command="ai_memory help", from_user=user, chat=group)
    text = "\n".join(request.text or "" for request in requests)
    assert "/ai_memory show 2" in text
    assert "/ai_memory sources 2" in text
    assert "/ai_memory correct FACT_ID I prefer short replies." in text
    assert "/ai_memory off" in text and "/ai_memory on" in text
    requests = await test_client.send_command(command="ai_memory show invalid", from_user=user, chat=group)
    assert any("page number" in (request.text or "") for request in requests)


async def test_feature_disabled_keeps_no_evidence(
    test_client: TestClient,
    test_services: ApplicationServices,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    await test_client.send_message(text="Existing behavior", from_user=user, chat=group)
    await test_client.send_command(command="ai_memory off", from_user=user, chat=group)
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document is None
