"""Unit tests for the standalone note-request predicate behind /cleannotes.

Only a message that is *nothing but* a note request may be deleted by the cleanup;
a note request mixed with other text is a real message and must survive.
"""

from __future__ import annotations

import pytest

from sophie_bot.modules.notes.utils.clean import is_standalone_note_request


@pytest.mark.parametrize(
    "text",
    [
        "#rules",
        "  #rules  ",
        "#Rules",
        "#note/sub",
        "/get rules",
        "/get #rules",
        "/get@SophieBot rules",
        "!get rules",
        "/get rules noformat",
        "/GET rules",
    ],
)
def test_standalone_note_requests(text: str) -> None:
    assert is_standalone_note_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "hey, see #rules",
        "#rules please read them",
        "#rules #faq",
        "#rules\nand something else",
        "/get rules and also hello",
        "/save rules text",
        "/notes",
        "rules",
        "#",
    ],
)
def test_non_standalone_note_requests(text: str | None) -> None:
    assert is_standalone_note_request(text) is False
