from __future__ import annotations

from typing import Any

import pytest

from sophie_bot.modules.ai.utils import chatbot_response
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _build_fitting_reply_doc
from sophie_bot.modules.ai.utils.mention_usernames import build_mention_index
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override


async def _render(text: str, *, redis: Any) -> str:
    doc = await chatbot_response.build_reply_doc(
        None,
        text,
        model=None,
        result=None,
        explicit_debug_mode=False,
        chat_tid=-1001509876,
        mention_index=build_mention_index(()),
        redis=redis,
        strip_alien_html_tags=True,
    )
    return doc.to_rich()


@pytest.mark.asyncio
async def test_wide_ai_table_becomes_bounded_key_value_cards(test_redis: Any, test_services: Any) -> None:
    headers = ["Name", *[f"Field {index}" for index in range(1, 16)]]
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    rows = [
        f"| item {index} | " + " | ".join(f"value {index}.{column}" for column in range(1, 16)) + " |"
        for index in range(1, 51)
    ]

    rich = await _render("\n".join([header, separator, *rows]), redis=test_redis)

    assert rich.count("<table bordered striped>") == 49
    assert rich.count("<tr>") == 49 * 15
    assert "<caption>item 49</caption>" in rich
    assert "item 50" not in rich
    assert "Field 15" not in rich
    assert "value 49.15" not in rich


@pytest.mark.asyncio
async def test_ai_table_overrides_and_protected_html(test_redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ai_chatbot_table_max_columns": 2,
        "ai_chatbot_table_max_rows": 2,
        "ai_chatbot_table_card_threshold": -1,
    }

    async def configured_value(feature: str, *, chat_tid: int | None, redis: Any) -> int:
        assert chat_tid == -1001509876
        return values[feature]

    monkeypatch.setattr(chatbot_response, "get_value", configured_value)
    text = (
        "| Name | Details | Discarded |\n"
        "| --- | --- | --- |\n"
        "| <b>one</b> | **kept** | lost |\n"
        "| two | discarded | lost |"
    )

    rich = await _render(text, redis=test_redis)

    assert rich.count("<table bordered striped compact>") == 1
    assert "<caption>" not in rich
    assert "<td><b>one</b></td><td><b>kept</b></td>" in rich
    assert "two" not in rich
    assert "Discarded" not in rich


@pytest.mark.parametrize("render_limit", [3900, 1], ids=["normal", "fallback"])
@pytest.mark.parametrize("ragged", [False, True], ids=["rectangular", "ragged"])
async def test_fitted_table_public_body_keeps_every_delivered_cell(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    render_limit: int,
    ragged: bool,
) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chatbot_reply.TELEGRAM_MESSAGE_SAFE_LIMIT", render_limit)
    for feature, value in (
        ("ai_chatbot_table_max_columns", 3),
        ("ai_chatbot_table_max_rows", 2),
        ("ai_chatbot_table_card_threshold", -1),
    ):
        await set_chat_override(feature, -1001509876, value, redis=test_services.redis)
    header = "| Name | Value |" if ragged else "| Name | Value | Extra | Omitted |"
    separator = "| --- | --- |" if ragged else "| --- | --- | --- | --- |"
    answer = f"{header}\n{separator}\n| kept | visible | extra_visible | secret_column |\n| unseen_row | secret_row |"

    doc, public_body = await _build_fitting_reply_doc(
        "quota",
        answer,
        model=None,
        result=None,
        explicit_debug_mode=False,
        chat_tid=-1001509876,
        services=test_services,
    )

    assert "extra_visible" in public_body
    assert public_body in doc.to_rich()
    assert "<td>kept</td><td>visible</td><td>extra_visible</td>" in public_body
    assert "secret" not in public_body and "unseen" not in public_body and "Omitted" not in public_body
    assert "quota" not in public_body
