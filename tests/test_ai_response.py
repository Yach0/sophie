from __future__ import annotations

import pytest
from stfu_tg import BlockQuote

from sophie_bot.modules.ai.utils.ai_header import AI_CHATBOT_CUSTOM_EMOJI_ID
from sophie_bot.modules.ai.utils.ai_response import render_ai_response
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME

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
