from types import SimpleNamespace

from sophie_bot.services import sentry_metrics
from sophie_bot.services.sentry_metrics import (
    _GAUGE_VALUES,
    _change_gauge_state,
    _make_gauge_key,
    _normalize_attributes,
    _set_gauge_state,
    change_gauge_metric,
    count_metric,
    distribution_metric,
    reset_gauge_metrics,
    sentry_metrics_enabled,
    set_gauge_metric,
)


class FakeMetrics:
    def __init__(self) -> None:
        self.count_calls: list[tuple[str, float, dict[str, object]]] = []
        self.distribution_calls: list[tuple[str, float, dict[str, object], str | None]] = []
        self.gauge_calls: list[tuple[str, float, dict[str, object], str | None]] = []

    def count(self, name: str, value: float, attributes: dict[str, object]) -> None:
        self.count_calls.append((name, value, attributes))

    def distribution(self, name: str, value: float, attributes: dict[str, object], unit: str | None) -> None:
        self.distribution_calls.append((name, value, attributes, unit))

    def gauge(self, name: str, value: float, attributes: dict[str, object], unit: str | None) -> None:
        self.gauge_calls.append((name, value, attributes, unit))


def enable_metrics(monkeypatch) -> FakeMetrics:
    fake_metrics = FakeMetrics()
    fake_sentry = SimpleNamespace(is_initialized=lambda: True, metrics=fake_metrics)
    monkeypatch.setattr(sentry_metrics, "sentry_sdk", fake_sentry)
    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_url", "https://sentry.example/1")
    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_enable_metrics", True)
    reset_gauge_metrics()
    return fake_metrics


def test_sentry_metrics_enabled_requires_dsn_flag_and_initialized_sdk(monkeypatch) -> None:
    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_url", "")
    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_enable_metrics", True)
    monkeypatch.setattr(sentry_metrics.sentry_sdk, "is_initialized", lambda: True)
    assert sentry_metrics_enabled() is False

    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_url", "https://sentry.example/1")
    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_enable_metrics", False)
    assert sentry_metrics_enabled() is False

    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_enable_metrics", True)
    monkeypatch.setattr(sentry_metrics.sentry_sdk, "is_initialized", lambda: False)
    assert sentry_metrics_enabled() is False

    monkeypatch.setattr(sentry_metrics.sentry_sdk, "is_initialized", lambda: True)
    assert sentry_metrics_enabled() is True


def test_normalize_attributes_removes_none_values() -> None:
    assert _normalize_attributes(None) == {}
    assert _normalize_attributes({"route": "/health", "skipped": None, "ok": True}) == {
        "route": "/health",
        "ok": True,
    }


def test_gauge_state_helpers_track_values_by_sorted_attributes() -> None:
    reset_gauge_metrics()
    attributes = {"worker": "a", "queue": "default"}

    _set_gauge_state("jobs", 2.0, attributes)
    next_value = _change_gauge_state("jobs", 3.5, {"queue": "default", "worker": "a"})

    assert next_value == 5.5
    assert _GAUGE_VALUES[_make_gauge_key("jobs", attributes)] == 5.5

    reset_gauge_metrics()
    assert _GAUGE_VALUES == {}


def test_metric_helpers_do_nothing_when_metrics_disabled(monkeypatch) -> None:
    fake_metrics = FakeMetrics()
    fake_sentry = SimpleNamespace(is_initialized=lambda: True, metrics=fake_metrics)
    monkeypatch.setattr(sentry_metrics, "sentry_sdk", fake_sentry)
    monkeypatch.setattr(sentry_metrics.CONFIG, "sentry_url", "")

    count_metric("requests")
    distribution_metric("duration", 12.0)
    set_gauge_metric("workers", 3.0)
    change_gauge_metric("workers", 1.0)

    assert fake_metrics.count_calls == []
    assert fake_metrics.distribution_calls == []
    assert fake_metrics.gauge_calls == []


def test_metric_helpers_forward_normalized_attributes_to_sentry(monkeypatch) -> None:
    fake_metrics = enable_metrics(monkeypatch)

    count_metric("requests", 2.0, {"route": "/start", "none": None})
    distribution_metric("duration", 12.5, {"route": "/start"}, unit="millisecond")
    set_gauge_metric("workers", 3.0, {"queue": "default"}, unit="worker")
    change_gauge_metric("workers", 2.0, {"queue": "default"}, unit="worker")

    assert fake_metrics.count_calls == [("requests", 2.0, {"route": "/start"})]
    assert fake_metrics.distribution_calls == [("duration", 12.5, {"route": "/start"}, "millisecond")]
    assert fake_metrics.gauge_calls == [
        ("workers", 3.0, {"queue": "default"}, "worker"),
        ("workers", 5.0, {"queue": "default"}, "worker"),
    ]
