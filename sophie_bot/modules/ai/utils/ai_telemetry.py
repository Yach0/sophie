from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import logfire

from sophie_bot.services.logfire import logfire_enabled


@contextmanager
def ai_span(name: str, **attributes: str | float | bool | None) -> Iterator[logfire.LogfireSpan | None]:
    if not logfire_enabled():
        yield None
        return
    with logfire.span(name, **cast(dict[str, Any], attributes)) as span:
        yield span


def ai_event(name: str, **attributes: str | float | bool | None) -> None:
    if logfire_enabled():
        logfire.info(name, **cast(dict[str, Any], attributes))
