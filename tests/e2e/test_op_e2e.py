from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_catalog import AICatalogModelModel, AICatalogProviderModel
from sophie_bot.modules.ai.schedules.generate_chat_summaries import GenerateChatSummaries
from tests.e2e.helpers import create_test_user_and_group


@pytest.mark.asyncio
async def test_op_regenerate_chat_summary_forces_generation(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000003, title="Op Regenerate Summarize Group")
    operator_wrapper = test_client.create_user(user_id=929500003, first_name="Operator", username="op_user_3")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None
    summary_date = datetime.now(UTC).date()

    with (
        patch.object(CONFIG, "operators", [operator_wrapper.user.id]),
        patch.object(
            GenerateChatSummaries,
            "process_chat",
            AsyncMock(),
        ) as process_chat,
    ):
        requests = await test_client.send_command(
            command="op_regenerate_chat_summary",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert process_chat.await_count == 1
    process_chat_chat, process_chat_date = process_chat.await_args.args[:2]
    assert process_chat_chat.tid == group_chat.id
    assert process_chat_date == summary_date
    assert process_chat.await_args.kwargs == {"force": True, "target_chat_tid": group_chat.id}
    assert requests, "Bot should respond to /op_regenerate_chat_summary"
    response_text = requests[-1].text or ""
    assert "Chat summary regenerated" in response_text


@pytest.mark.asyncio
async def test_op_ai_model_sets_updates_and_lists_registered_capacity(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, group, _ = await create_test_user_and_group(test_client)
    monkeypatch.setattr(CONFIG, "operators", [user.id])
    await AICatalogProviderModel(name="custom", api_key="offline").save()

    requests = await test_client.send_command(
        command="op_ai_model",
        args="^provider=custom ^context_window_tokens=32768 custom/model",
        from_user=user,
        chat=group,
    )
    sends = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert sends and "32768" in (sends[-1].text or "")
    assert sends[-1].params["chat_id"] == group.id
    stored = await AICatalogModelModel.find_one(AICatalogModelModel.name == "custom/model")
    assert stored is not None and stored.context_window_tokens == 32768

    marker = "_migration_add_ai_model_context_sizes"
    collection = AICatalogModelModel.get_pymongo_collection()
    await collection.update_one(
        {"name": "custom/model"},
        {"$set": {marker: {"context_window_tokens": 32768, "openrouter_id": "custom/model", "was_null": False}}},
    )
    await test_client.send_command(
        command="op_ai_model",
        args="^context_window_tokens=32768 custom/model",
        from_user=user,
        chat=group,
    )
    assert marker not in await collection.find_one({"name": "custom/model"})

    await test_client.send_command(
        command="op_ai_model",
        args="^context_window_tokens=65536 custom/model",
        from_user=user,
        chat=group,
    )
    await test_client.send_command(
        command="op_ai_model", args="^images=no custom/model", from_user=user, chat=group
    )
    stored = await AICatalogModelModel.find_one(AICatalogModelModel.name == "custom/model")
    assert stored.context_window_tokens == 65536
    assert stored.supports_images is False

    await test_client.send_command(
        command="op_ai_model", args="^provider=custom custom/unconfigured", from_user=user, chat=group
    )
    unconfigured = await AICatalogModelModel.find_one(AICatalogModelModel.name == "custom/unconfigured")
    assert unconfigured is not None and unconfigured.context_window_tokens is None

    requests = await test_client.send_command(command="op_ai_models", from_user=user, chat=group)
    sends = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert sends
    assert "65536" in (sends[-1].text or "")
    assert "custom/unconfigured" in (sends[-1].text or "")
    assert "unset" in (sends[-1].text or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [0, -1])
@pytest.mark.parametrize("existing", [False, True])
async def test_op_ai_model_rejects_nonpositive_capacity_without_mutation(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch, capacity: int, existing: bool
) -> None:
    user, group, _ = await create_test_user_and_group(test_client)
    monkeypatch.setattr(CONFIG, "operators", [user.id])
    if existing:
        await AICatalogModelModel(name="custom/model", provider="custom", context_window_tokens=32768).save()

    requests = await test_client.send_command(
        command="op_ai_model",
        args=f"^provider=custom ^context_window_tokens={capacity} custom/model",
        from_user=user,
        chat=group,
    )
    sends = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert sends and "must be a positive number" in (sends[-1].text or "")
    stored = await AICatalogModelModel.find_one(AICatalogModelModel.name == "custom/model")
    if existing:
        assert stored is not None and stored.context_window_tokens == 32768
    else:
        assert stored is None


@pytest.mark.asyncio
async def test_op_ai_model_capacity_cannot_be_changed_by_nonoperator(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, group, _ = await create_test_user_and_group(test_client)
    monkeypatch.setattr(CONFIG, "operators", [])
    await AICatalogModelModel(name="custom/model", provider="custom", context_window_tokens=32768).save()

    requests = await test_client.send_command(
        command="op_ai_model",
        args="^context_window_tokens=65536 custom/model",
        from_user=user,
        chat=group,
    )
    assert not any(
        request.request_type == RequestType.SEND_MESSAGE and "AI Model saved" in (request.text or "")
        for request in requests
    )
    stored = await AICatalogModelModel.find_one(AICatalogModelModel.name == "custom/model")
    assert stored is not None and stored.context_window_tokens == 32768
