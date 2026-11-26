from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from sophie_bot.services import sentry


class CapturingTransport(Transport):
    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__()
        self.events = events

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.headers.get("type") == "event" and item.payload.json is not None:
                self.events.append(item.payload.json)


@pytest.fixture
def runtime_sentry_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Use runtime integrations and filtering, replacing only the outbound transport."""
    events: list[dict[str, Any]] = []
    actual_init = sentry_sdk.init

    def init_with_transport(*args: Any, **kwargs: Any) -> None:
        actual_init(*args, **kwargs, transport=CapturingTransport(events))

    monkeypatch.setattr(sentry_sdk, "init", init_with_transport)
    monkeypatch.setattr(sentry.CONFIG, "sentry_url", "https://public@sentry.invalid/1")
    monkeypatch.setattr(sentry.CONFIG, "sentry_traces_sample_rate", 0.0)
    monkeypatch.setattr(sentry.CONFIG, "sentry_profile_session_sample_rate", 0.0)
    with sentry_sdk.isolation_scope() as scope:
        scope.clear()
        sentry.init_sentry()
        client = sentry_sdk.get_client()
        try:
            yield events
        finally:
            client.close()
            sentry_sdk.get_global_scope().set_client(None)
