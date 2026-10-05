from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.filters.command import CommandException, CommandObject
from aiogram.types import Chat, Message, User

from sophie_bot.filters.cmd import CMDFilter


@pytest.fixture
def bot() -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.me.return_value = User(id=123, is_bot=True, first_name="Sophie", username="SophieBot")
    return bot


@pytest.mark.parametrize("registered_name", ["resetallwarns", "reset_all_warns", "reset-all-warns"])
@pytest.mark.parametrize("spelling", ["resetallwarns", "reset_all_warns", "reset-all-warns"])
async def test_parse_literal_ignores_separators_and_preserves_command(
    registered_name: str, spelling: str, bot: AsyncMock
) -> None:
    command_filter = CMDFilter(registered_name, prefix="!", ignore_mention=False)
    args = " reason_with-words  @Someone\nsecond line "

    command = await command_filter.parse_command(f"!{spelling}@sOpHiEbOt {args}", bot)

    assert command == CommandObject(prefix="!", command=spelling, mention="sOpHiEbOt", args=args)
    bot.me.assert_awaited_once()


async def test_production_filter_returns_original_command(bot: AsyncMock) -> None:
    command_filter = CMDFilter("reset_all_warns", prefix="/", ignore_mention=False)
    message = MagicMock(spec=Message)
    message.text = "/reset-all-warns@SophieBot"
    message.forward_from = None
    message.entities = None
    chat = Chat(id=-100123, type="supergroup")

    result = await command_filter(message, bot, chat)

    assert result == {"command": CommandObject(prefix="/", command="reset-all-warns", mention="SophieBot", args=None)}


@pytest.mark.parametrize("ignore_case", [True, False])
@pytest.mark.parametrize(
    ("registered_name", "spelling", "matches_case"),
    [
        ("reset_all_warns", "ReSeT-All-WaRnS", False),
        ("ReSeT_All_WaRnS", "reset-all-warns", False),
        ("ReSeT_All_WaRnS", "rEsEt-aLl-wArNs", False),
        ("ReSeT_All_WaRnS", "ReSeT-All-WaRnS", True),
        ("Stra_ße", "STRAS-SE", False),
    ],
)
async def test_literal_case_matching(
    registered_name: str, spelling: str, matches_case: bool, ignore_case: bool, bot: AsyncMock
) -> None:
    command_filter = CMDFilter(registered_name, prefix="!", ignore_case=ignore_case, ignore_mention=False)
    args = " reason_with-words  @Someone\nsecond line "
    text = f"!{spelling}@sOpHiEbOt {args}"

    if not ignore_case and not matches_case:
        with pytest.raises(CommandException, match="Command did not match pattern"):
            await command_filter.parse_command(text, bot)
        return

    command = await command_filter.parse_command(text, bot)

    assert command == CommandObject(prefix="!", command=spelling, mention="sOpHiEbOt", args=args)
    bot.me.assert_awaited_once()


@pytest.mark.parametrize("spelling", ["reset.all.warns", "resetallwarn", "ResetAllWarns"])
async def test_literal_still_rejects_other_spellings(spelling: str, bot: AsyncMock) -> None:
    command_filter = CMDFilter("reset_all_warns", prefix="/", ignore_case=False)

    with pytest.raises(CommandException, match="Command did not match pattern"):
        await command_filter.parse_command(f"/{spelling}", bot)


@pytest.mark.parametrize("ignore_case", [True, False])
@pytest.mark.parametrize(
    ("pattern", "spelling", "matches"),
    [
        (r"reset_all_warns$", "reset_all_warns", True),
        (r"reset-all-warns$", "reset-all-warns", True),
        (r"reset_all_warns$", "resetallwarns", False),
        (r"resetallwarns$", "reset_all_warns", False),
        (r"resetallwarns$", "reset-all-warns", False),
        (r"reset(?P<separator>[_-])", "reset-all-warns", True),
        (r"reset_all_warns$", "ReSeT_All_WaRnS", False),
        (r"(?i)reset_all_warns$", "ReSeT_All_WaRnS", True),
        (r"(?i)reset_all_warns$", "ReSeT-All-WaRnS", False),
    ],
)
async def test_regex_matches_original_spelling(
    pattern: str, spelling: str, matches: bool, ignore_case: bool, bot: AsyncMock
) -> None:
    # A preceding literal must not normalize the object seen by the regex.
    command_filter = CMDFilter(
        ("other_command", re.compile(pattern)), prefix="/", ignore_case=ignore_case, ignore_mention=False
    )
    text = f"/{spelling}@SophieBot reason_with-hyphens"

    if not matches:
        with pytest.raises(CommandException, match="Command did not match pattern"):
            await command_filter.parse_command(text, bot)
        return

    command = await command_filter.parse_command(text, bot)

    assert command.command == spelling
    assert command.prefix == "/"
    assert command.mention == "SophieBot"
    assert command.args == "reason_with-hyphens"
    assert command.regexp_match is not None
    assert command.regexp_match.string == spelling
    expected_match = re.match(pattern, spelling)
    assert expected_match is not None
    assert command.regexp_match.group(0) == expected_match.group(0)
    assert command.regexp_match.groupdict() == expected_match.groupdict()
