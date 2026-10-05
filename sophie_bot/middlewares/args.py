from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import iscoroutinefunction
from typing import Any

from aiogram.dispatcher.flags import get_flag
from aiogram.filters.command import CommandObject
from aiogram.types import Message, TelegramObject
from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ARGS_EXCEPTIONS
from ass_tg.i18n import gettext_ctx
from ass_tg.middleware import ArgsMiddleware as ASSArgsMiddleware
from ass_tg.types import AndArg
from ass_tg.types.base_abc import ArgFabric


class ArgsMiddleware(ASSArgsMiddleware):
    """Parse argument entities from the wire text, independently of command spelling."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        update: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(update, Message) and (base_arg := get_flag(data, "args")):
            if iscoroutinefunction(base_arg):
                base_arg = await base_arg(update, data)

            if isinstance(base_arg, dict):
                base_arg = AndArg(**base_arg)
            elif not isinstance(base_arg, ArgFabric):
                raise ValueError("args must be an ASS argument fabric or schema")

            command: CommandObject | None = data.get("command")
            text = (command.args or "") if command else ""
            raw_text = update.text or update.caption or ""
            raw_entities = update.entities if update.text else update.caption_entities
            entities = ArgEntities(raw_entities or [])
            if command:
                # args is the unchanged suffix of the wire text. Telegram offsets use
                # UTF-16 units; this includes the raw command, mention and whitespace.
                argument_offset = (len(raw_text.encode("utf-16-le")) - len(text.encode("utf-16-le"))) // 2
                entities = entities.cut_before(argument_offset)

            with self.i18n.context():
                gettext_ctx.set(self.i18n)
                try:
                    arg = await base_arg(text, 0, entities, command=command)
                    data["arg"] = arg
                    for arg_name, arg_data in arg.value.items():
                        data[arg_name] = arg_data.get_value()
                except ARGS_EXCEPTIONS as error:
                    await self.send_error(update, error, command)
                    return None

        return await handler(update, data)
