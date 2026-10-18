from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message
from stfu_tg import BlockQuote, Doc

from sophie_bot.modules.ai.magic_handlers import modern_action
from sophie_bot.modules.ai.utils.ai_header import AI_CHATBOT_CUSTOM_EMOJI_ID, AI_CUSTOM_EMOJI_ID
from sophie_bot.modules.ai.utils.ai_response import render_ai_response
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME
from sophie_bot.modules.filters.enforce_middleware import EnforceFiltersMiddleware
from sophie_bot.services.application import ApplicationServices

MARKDOWN = "**First** & `code`.\n\nSecond *paragraph*.\n\nThird [link](https://example.com)."
PARAGRAPHS = '<p>Second <i>paragraph</i>.</p>\n<p>Third <a href="https://example.com">link</a>.</p>'


@pytest.mark.parametrize("header", [None, "quota"])
def test_shared_composition_keeps_paragraphs_metadata_and_trailing_blocks(header: str | None) -> None:
    doc = render_ai_response(
        header,
        MARKDOWN,
        BlockQuote("Source <title>", expandable=True),
        tool_labels=(AI_TOOLS_BY_NAME["web_search"],),
    )
    rich = doc.to_rich()
    assert PARAGRAPHS in rich
    assert "<blockquote expandable>Source &lt;title&gt;</blockquote>" in rich
    if header is None:
        assert rich.startswith("<p><b>First</b> &amp; <code>code</code>.</p>\n")
        assert "<tg-emoji" not in rich
    else:
        assert rich.startswith(f'<tg-emoji emoji-id="{AI_CHATBOT_CUSTOM_EMOJI_ID}">')
        assert "(Search) <b>First</b> &amp; <code>code</code>.\n" in rich
    assert "<p>" not in doc.to_html()
    assert "**First**" in doc.to_md()


@pytest.mark.parametrize("strip_html", [False, True])
def test_shared_html_policy_preserves_code_and_escapes_untrusted_text(strip_html: bool) -> None:
    doc = render_ai_response(
        None,
        "<alien>Text</alien> <i>italic</i> & value\n\n`<alien>literal</alien>`",
        strip_alien_html_tags=strip_html,
    )
    rich = doc.to_rich()
    assert "<code>&lt;alien&gt;literal&lt;/alien&gt;</code>" in rich
    assert "&amp; value" in rich
    if strip_html:
        assert rich.startswith("<p>Text <i>italic</i> &amp; value</p>")
    else:
        assert "&lt;alien&gt;Text&lt;/alien&gt;" in rich


async def test_filter_ai_action_final_payload_keeps_title_and_rich_paragraphs(monkeypatch: pytest.MonkeyPatch) -> None:
    send = AsyncMock()
    message = cast(
        Message,
        SimpleNamespace(
            text="trigger",
            chat=SimpleNamespace(id=-100123),
            message_id=7,
            message_thread_id=8,
            bot=SimpleNamespace(send_rich_message=send),
        ),
    )
    chat = SimpleNamespace(iid="chat", tid=-100123)
    services = SimpleNamespace(redis=object())
    history = MagicMock()
    history.add_from_cache = AsyncMock()
    history.add_from_message = AsyncMock()
    monkeypatch.setattr(modern_action.ChatModel, "get_by_tid", AsyncMock(return_value=chat))
    monkeypatch.setattr(modern_action.AIQuotaFilter, "__call__", AsyncMock(return_value=True))
    monkeypatch.setattr(modern_action, "OldContext", MagicMock(return_value=history))
    monkeypatch.setattr(
        modern_action, "get_chat_default_model_plan", AsyncMock(return_value=SimpleNamespace(primary="test"))
    )
    monkeypatch.setattr(modern_action, "Agent", MagicMock())
    monkeypatch.setattr(
        modern_action, "run_ai_text", AsyncMock(return_value=SimpleNamespace(output=MARKDOWN, usage=None))
    )
    body = await modern_action.AIReplyAction().handle(
        message,
        {"context": SimpleNamespace(connection=SimpleNamespace(tid=-100123, db_model=chat)), "services": services},
        modern_action.AIReplyActionDataModel(prompt="Respond"),
    )
    assert isinstance(body, Doc)
    # Actions return a body to the filter aggregator; they must not add their own metadata.
    assert "<tg-emoji" not in body.to_rich()
    assert "AI Response" in body.to_rich()
    await EnforceFiltersMiddleware._handle_action_messages(
        message,
        [body],
        services=cast(ApplicationServices, services),
        ai_matched=True,
    )
    payload = send.await_args.kwargs
    assert PARAGRAPHS in payload["rich_message"].html
    assert "<b>First</b> &amp; <code>code</code>." in payload["rich_message"].html
    assert payload["reply_parameters"].message_id == 7
    assert payload["message_thread_id"] == 8
    assert AI_CUSTOM_EMOJI_ID in payload["rich_message"].html
    assert AI_CHATBOT_CUSTOM_EMOJI_ID not in payload["rich_message"].html
