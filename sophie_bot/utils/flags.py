from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiogram import flags as aiogram_flags
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.dispatcher.flags import get_flag


class BoundFlagDecorator:
    def __init__(self, decorator: Any) -> None:
        self._decorator = decorator

    def __call__[Decorated: Callable[..., Any]](self, decorated: Decorated, /) -> Decorated:
        return self._decorator(decorated)


class FlagDecorator:
    def __init__(self, decorator: Any) -> None:
        self._decorator = decorator

    def __call__(self, **kwargs: Any) -> BoundFlagDecorator:
        return BoundFlagDecorator(self._decorator(**kwargs))


args = FlagDecorator(aiogram_flags.args)
ai_cache = FlagDecorator(aiogram_flags.ai_cache)
ai_chatbot_response = FlagDecorator(aiogram_flags.ai_chatbot_response)
disableable = FlagDecorator(aiogram_flags.disableable)
help = FlagDecorator(aiogram_flags.help)
status = FlagDecorator(aiogram_flags.status)


def get_disableable_name(source: HandlerObject | dict[str, Any]) -> str | None:
    """Returns the canonical identity of a disable-able command.

    This is the only key allowed to reach the database: everything that stores, lists, or enforces a
    disabled command must derive it from here, otherwise the stored key and the enforced key diverge.
    """
    flag = get_flag(source, "disableable")
    return flag.name if flag else None
