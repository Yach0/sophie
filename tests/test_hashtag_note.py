from __future__ import annotations

import re

import pytest

from sophie_bot.modules.notes.handlers.get import HashtagGetNote


class TestHashtagGetNoteRegex:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("#hello", ["hello"]),
            ("#docs/readme", ["docs/readme"]),
            ("#note.v2", ["note.v2"]),
            ("#c++", ["c++"]),
            ("#key=value", ["key=value"]),
            ("#note1 #docs/readme", ["note1", "docs/readme"]),
            ("Check #docs/readme. Hello", ["docs/readme"]),
            ("#my-note", ["my-note"]),
            ("no hashtag here", []),
        ],
    )
    def test_hashtag_regex_extracts_note_names(self, text: str, expected: list[str]) -> None:
        assert HashtagGetNote.hashtag_regex.findall(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "#hello",
            "#docs/readme",
            "#note.v2",
            "#c++",
            "#key=value",
            "before #docs/readme after",
            "#my-note",
        ],
    )
    def test_filter_regex_matches_special_character_hashtags(self, text: str) -> None:
        filter_regex = re.compile(HashtagGetNote.hashtag_filter_pattern)

        assert filter_regex.match(text)

    def test_filter_regex_does_not_match_text_without_hashtag(self) -> None:
        filter_regex = re.compile(HashtagGetNote.hashtag_filter_pattern)

        assert not filter_regex.match("no hashtag here")
