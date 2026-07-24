from __future__ import annotations

from collections.abc import Callable
from typing import cast


class RuntimeProxy[T]:
    def __init__(self, getter: Callable[[], T]) -> None:
        self._getter = getter

    def _target(self) -> T:
        return self._getter()

    def __call__(self, *args: object, **kwargs: object) -> object:
        target = cast(Callable[..., object], self._target())
        return target(*args, **kwargs)

    def __getattr__(self, item: str):
        return getattr(self._target(), item)

    def __repr__(self) -> str:
        return repr(self._target())

    def __dir__(self) -> list[str]:
        return dir(self._target())
