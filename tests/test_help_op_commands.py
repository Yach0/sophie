from __future__ import annotations

import pytest
from aiogram import Router

from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.help.handlers import op
from sophie_bot.modules.help.utils.extract_info import HandlerHelp, ModuleHelp, gather_cmds_help


def _handler(command: str, description: str = "description") -> HandlerHelp:
    return HandlerHelp(
        cmds=(command,),
        args=None,
        description=description,
        only_admin=False,
        only_op=False,
        only_pm=False,
        only_chats=False,
        alias_to_modules=[],
        disableable=None,
    )


def _module(name: str, handlers: list[HandlerHelp]) -> ModuleHelp:
    return ModuleHelp(
        handlers=handlers,
        name=name,
        icon="?",
        exclude_public=False,
        info="",
        description="",
        advertise_wiki_page=False,
    )


def test_format_op_commands_messages_splits_between_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    first_module = _module("First", [_handler("first")])
    second_module = _module("Second", [_handler("second")])
    first_module_text = op._format_module_commands(first_module)
    second_module_text = op._format_module_commands(second_module)
    limit = max(len(first_module_text), len(second_module_text)) + 5

    monkeypatch.setattr(op, "OP_COMMANDS_MESSAGE_LENGTH_LIMIT", limit)

    assert op.format_op_commands_messages([first_module, second_module]) == [first_module_text, second_module_text]


def test_format_op_commands_messages_splits_large_module_by_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    first_handler = _handler("first", "first description " * 8)
    second_handler = _handler("second", "second description " * 8)
    module = _module("Large", [first_handler, second_handler])
    first_handler_text = op._format_module_commands(module, [first_handler])
    second_handler_text = op._format_module_commands(module, [second_handler])
    limit = max(len(first_handler_text), len(second_handler_text)) + 5

    monkeypatch.setattr(op, "OP_COMMANDS_MESSAGE_LENGTH_LIMIT", limit)

    assert op.format_op_commands_messages([module]) == [first_handler_text, second_handler_text]


@pytest.mark.asyncio
async def test_gather_cmds_help_marks_private_chat_type_as_pm_only() -> None:
    router = Router()

    async def private_handler() -> None:
        return None

    router.message.register(private_handler, CMDFilter("private"), ChatTypeFilter("private"))

    helps = await gather_cmds_help(router)

    assert len(helps) == 1
    assert helps[0].only_pm is True
    assert helps[0].only_chats is False


@pytest.mark.asyncio
async def test_gather_cmds_help_marks_inverted_private_chat_type_as_chats_only() -> None:
    router = Router()

    async def group_handler() -> None:
        return None

    router.message.register(group_handler, CMDFilter("group"), ~ChatTypeFilter("private"))

    helps = await gather_cmds_help(router)

    assert len(helps) == 1
    assert helps[0].only_pm is False
    assert helps[0].only_chats is True
