from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, F, Router
from aiogram.filters import MagicData
from aiogram.filters.command import CommandException, CommandObject
from aiogram.types import Chat, Message, MessageEntity, User
from ass_tg.types import AndArg, TextArg

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.middlewares.args import ArgsMiddleware
from sophie_bot.utils.i18n import I18nNew


@pytest.fixture
def bot() -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.me.return_value = User(id=123, is_bot=True, first_name="Sophie", username="SophieBot")
    return bot


@pytest.mark.parametrize("registered_name", ["resetallwarns", "reset_all_warns", "reset-all-warns"])
@pytest.mark.parametrize("spelling", ["resetallwarns", "reset_all_warns", "reset-all-warns"])
async def test_parse_literal_returns_registered_name_and_preserves_metadata(
    registered_name: str, spelling: str, bot: AsyncMock
) -> None:
    command_filter = CMDFilter(registered_name, prefix="!", ignore_mention=False)
    args = " reason_with-words  @Someone\nsecond line "

    command = await command_filter.parse_command(f"!{spelling}@sOpHiEbOt {args}", bot)

    assert command == CommandObject(prefix="!", command=registered_name, mention="sOpHiEbOt", args=args)
    bot.me.assert_awaited_once()


async def test_production_filter_returns_registered_command(bot: AsyncMock) -> None:
    command_filter = CMDFilter("reset_all_warns", prefix="/", ignore_mention=False)
    message = MagicMock(spec=Message)
    message.text = "/reset-all-warns@SophieBot"
    message.forward_from = None
    message.entities = None
    chat = Chat(id=-100123, type="supergroup")

    result = await command_filter(message, bot, chat)

    assert result == {"command": CommandObject(prefix="/", command="reset_all_warns", mention="SophieBot", args=None)}


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

    assert command == CommandObject(prefix="!", command=registered_name, mention="sOpHiEbOt", args=args)
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


@pytest.mark.parametrize(
    ("aliases", "spelling", "registered_name"),
    [
        (("resetwarns", "delwarns"), "RESET_WARNS", "resetwarns"),
        (("resetwarns", "delwarns"), "DEL-WARNS", "delwarns"),
        (("cban", "scban"), "C_BAN", "cban"),
        (("cban", "scban"), "S-C_BAN", "scban"),
        (("fban", "sfban"), "F-BAN", "fban"),
        (("fban", "sfban"), "S_F-BAN", "sfban"),
        (("stban", "tsban"), "S_T-BAN", "stban"),
        (("stban", "tsban"), "T-S_BAN", "tsban"),
    ],
)
async def test_literal_aliases_return_the_matched_registered_literal(
    aliases: tuple[str, ...], spelling: str, registered_name: str, bot: AsyncMock
) -> None:
    command = await CMDFilter(aliases, prefix="/", ignore_case=True).parse_command(f"/{spelling}", bot)

    assert command.command == registered_name


def test_literal_replacement_preserves_all_metadata_without_mutating_input() -> None:
    regexp_match = re.match(".*", "raw")
    original = CommandObject(
        prefix="!",
        command="RESET-ALL-WARNS",
        mention="SophieBot",
        args="  reason_with-hyphens ",
        regexp_match=regexp_match,
        magic_result={"value": 42},
    )

    result = CMDFilter("reset_all_warns", ignore_case=True).validate_command(original)

    assert result is not original
    assert result.command == "reset_all_warns"
    assert original.command == "RESET-ALL-WARNS"
    assert result.prefix == original.prefix
    assert result.mention == original.mention
    assert result.args == original.args
    assert result.regexp_match is original.regexp_match
    assert result.magic_result is original.magic_result


async def test_magic_filter_sees_registered_literal(bot: AsyncMock) -> None:
    command_filter = CMDFilter("reset_all_warns", prefix="/", magic=F.command == "reset_all_warns")

    command = await command_filter.parse_command("/reset-all-warns", bot)

    assert command.command == "reset_all_warns"


async def test_wrong_mention_is_rejected_before_literal_matching(bot: AsyncMock) -> None:
    command_filter = CMDFilter("scban", prefix="/", ignore_case=True, ignore_mention=False)

    with pytest.raises(CommandException, match="Mention did not match"):
        await command_filter.parse_command("/S-CBAN@OtherBot reason", bot)

    bot.me.assert_awaited_once()


async def test_ignored_mention_is_preserved(bot: AsyncMock) -> None:
    command = await CMDFilter("scban", prefix="/", ignore_case=True, ignore_mention=True).parse_command(
        "/S-CBAN@OtherBot reason", bot
    )

    assert command == CommandObject(prefix="/", command="scban", mention="OtherBot", args="reason")
    bot.me.assert_not_awaited()


async def test_router_passes_canonical_command_through_args_middleware(bot: AsyncMock, i18n_context: I18nNew) -> None:
    parent_router = Router()
    child_router = Router()
    parent_router.include_router(child_router)
    parent_router.message.middleware(ArgsMiddleware(i18n=i18n_context))
    seen_by_args: list[CommandObject] = []

    async def argument_schema(message: Message, data: dict[str, object]) -> dict[str, TextArg]:
        command = data["command"]
        assert isinstance(command, CommandObject)
        seen_by_args.append(command)
        return {"reason": TextArg("Reason")}

    async def handler(message: Message, command: CommandObject, reason: str) -> tuple[CommandObject, str]:
        return command, reason

    child_router.message.register(
        handler,
        CMDFilter(("fban", "sfban"), prefix="!", ignore_case=True, ignore_mention=False),
        MagicData(F.command.command == "sfban"),
        flags={"args": argument_schema},
    )
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="supergroup"),
        text="!S_F-BAN@sOpHiEbOt reason_with-hyphens  second line",
    )

    result = await parent_router.propagate_event(update_type="message", event=message, bot=bot, event_chat=message.chat)

    expected = CommandObject(prefix="!", command="sfban", mention="sOpHiEbOt", args="reason_with-hyphens  second line")
    assert result == (expected, expected.args)
    assert seen_by_args == [expected]
    assert result[0] is seen_by_args[0]


@pytest.mark.parametrize(
    ("registered_name", "spelling", "is_regex"),
    [
        ("setrules", "set_rules", False),
        ("setrules", "set--rules", False),
        ("set_rules", "setrules", False),
        ("set--rules", "set_rules", False),
        ("setrules", "setrules", False),
        ("setrules", "SET_RULES", False),
        ("strasse", "stra_ße", False),
        ("set😀rules", "set_😀rules", False),
        ("setrules", "set-rules", True),
    ],
)
@pytest.mark.parametrize("mention", ["", "@sOpHiEbOt"])
@pytest.mark.parametrize("caption", [False, True])
@pytest.mark.parametrize("schema_kind", ["dynamic", "dict", "fabric"])
async def test_router_preserves_argument_entities_and_canonical_command(
    registered_name: str,
    spelling: str,
    is_regex: bool,
    mention: str,
    caption: bool,
    schema_kind: str,
    bot: AsyncMock,
    i18n_context: I18nNew,
) -> None:
    parent_router = Router()
    child_router = Router()
    parent_router.include_router(child_router)
    parent_router.message.middleware(ArgsMiddleware(i18n=i18n_context))
    seen_by_schema: list[CommandObject] = []
    expected_name = spelling if is_regex else registered_name

    async def argument_schema(message: Message, data: dict[str, object]) -> dict[str, TextArg]:
        command = data["command"]
        assert isinstance(command, CommandObject)
        assert command.command == expected_name
        seen_by_schema.append(command)
        return {"reason": TextArg("Reason", parse_entities=True)}

    async def handler(message: Message, command: CommandObject, reason: str) -> tuple[CommandObject, str]:
        return command, reason

    schema = {"reason": TextArg("Reason", parse_entities=True)}
    args_flag = argument_schema if schema_kind == "dynamic" else AndArg(**schema) if schema_kind == "fabric" else schema
    pattern = re.compile(r"set(?P<separator>-)rules$")
    child_router.message.register(
        handler,
        CMDFilter(("other_command", pattern) if is_regex else registered_name, prefix="/", allow_caption=caption),
        MagicData(F.command.command == expected_name),
        flags={"args": args_flag},
    )
    token = f"/{spelling}{mention}"
    text = f"{token} hello 😀 world"
    argument_offset = len(token.encode("utf-16-le")) // 2 + 1
    entities = [
        MessageEntity(type="bot_command", offset=0, length=argument_offset - 1),
        MessageEntity(type="bold", offset=argument_offset, length=5),
        MessageEntity(type="italic", offset=argument_offset + 9, length=5),
    ]
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="supergroup"),
        **({"caption": text, "caption_entities": entities} if caption else {"text": text, "entities": entities}),
    )
    original_message = message.model_dump()

    command, reason = await parent_router.propagate_event(
        update_type="message", event=message, bot=bot, event_chat=message.chat
    )

    assert reason == "<b>hello</b> 😀 <i>world</i>"
    assert command.command == expected_name
    assert type(command.command) is str
    assert command.mention == mention.removeprefix("@")
    assert command.args == "hello 😀 world"
    if schema_kind == "dynamic":
        assert seen_by_schema == [command]
        assert seen_by_schema[0] is command
    if is_regex:
        assert command.regexp_match is not None
        assert command.regexp_match.string == spelling
        assert command.regexp_match.groupdict() == {"separator": "-"}
    else:
        assert command.regexp_match is None
    assert message.model_dump() == original_message


@pytest.mark.parametrize("entity_type", ["code", "pre"])
async def test_caption_command_respects_ignore_code(entity_type: str, bot: AsyncMock) -> None:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="supergroup"),
        caption="/set_rules hello",
        caption_entities=[MessageEntity(type=entity_type, offset=0, length=10)],
    )

    assert (
        await CMDFilter("setrules", prefix="/", allow_caption=True, ignore_code=True)(message, bot, message.chat)
        is False
    )
    assert await CMDFilter("setrules", prefix="/", allow_caption=True, ignore_code=False)(message, bot, message.chat)
