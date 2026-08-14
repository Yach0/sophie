"""Unit tests for the standalone note-request predicates behind /cleannotes.

Only a message that is *nothing but* a note request may be deleted by the cleanup;
a note request mixed with other text is a real message and must survive.
"""

from __future__ import annotations

import pytest
from aiogram.filters import CommandObject

from sophie_bot.modules.notes.utils.clean import (
    is_standalone_command_request,
    is_standalone_hashtag_request,
)


def _command(args: str | None, *, mention: str = "") -> CommandObject:
    return CommandObject(prefix="/", command="get", mention=mention, args=args)


@pytest.mark.parametrize(
    ("args", "note_name"),
    [
        ("rules", "rules"),
        ("#rules", "rules"),
        ("  rules  ", "rules"),
        ("Rules", "Rules"),
        ("note/sub", "note/sub"),
        ("rules noformat", "rules"),
        ("rules    noformat", "rules"),
        ("rules ?raw", "rules"),
    ],
)
def test_standalone_command_requests(args: str, note_name: str) -> None:
    assert is_standalone_command_request(_command(args), note_name) is True


def test_standalone_command_request_with_bot_mention() -> None:
    """aiogram keeps the mention out of the args, so `/get@SophieBot rules` is still standalone."""

    assert is_standalone_command_request(_command("rules", mention="SophieBot"), "rules") is True


@pytest.mark.parametrize(
    ("args", "note_name"),
    [
        ("rules and also hello", "rules"),
        ("rules noformat please", "rules"),
        ("rules raw", "rules"),
        (None, "rules"),
        ("", "rules"),
        ("   ", "rules"),
    ],
)
def test_non_standalone_command_requests(args: str | None, note_name: str) -> None:
    assert is_standalone_command_request(_command(args), note_name) is False


def test_missing_command_is_not_standalone() -> None:
    assert is_standalone_command_request(None, "rules") is False


@pytest.mark.parametrize(
    ("text", "note_names"),
    [
        ("#rules", ["rules"]),
        ("  #rules  ", ["rules"]),
        ("#Rules", ["Rules"]),
        ("#note/sub", ["note/sub"]),
    ],
)
def test_standalone_hashtag_requests(text: str, note_names: list[str]) -> None:
    assert is_standalone_hashtag_request(text, note_names) is True


@pytest.mark.parametrize(
    ("text", "note_names"),
    [
        (None, []),
        ("", []),
        ("hey, see #rules", ["rules"]),
        ("#rules please read them", ["rules"]),
        ("#rules #faq", ["rules", "faq"]),
        ("#rules\nand something else", ["rules"]),
        ("#rules.", ["rules"]),
        ("#rules", []),
    ],
)
def test_non_standalone_hashtag_requests(text: str | None, note_names: list[str]) -> None:
    assert is_standalone_hashtag_request(text, note_names) is False
