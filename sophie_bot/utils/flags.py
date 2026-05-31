from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, overload

from aiogram import flags as aiogram_flags

_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])
_ClassT = TypeVar("_ClassT", bound=type[Any])


class FlagDecorator:
    def __init__(self, decorator: Any) -> None:
        self._decorator = decorator

    @overload
    def __call__(self, value: _ClassT, /) -> _ClassT: ...

    @overload
    def __call__(self, value: _CallableT, /) -> _CallableT: ...

    @overload
    def __call__(self, value: Any, /) -> FlagDecorator: ...

    @overload
    def __call__(self, **kwargs: Any) -> FlagDecorator: ...

    def __call__(self, value: Any | None = None, **kwargs: Any) -> Any:
        result = self._decorator(value, **kwargs)
        if value is not None and callable(value):
            return result

        return FlagDecorator(result)


args = FlagDecorator(aiogram_flags.args)
ai_cache = FlagDecorator(aiogram_flags.ai_cache)
ai_chatbot_response = FlagDecorator(aiogram_flags.ai_chatbot_response)
disableable = FlagDecorator(aiogram_flags.disableable)
help = FlagDecorator(aiogram_flags.help)
status = FlagDecorator(aiogram_flags.status)
