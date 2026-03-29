from __future__ import annotations

from threading import Lock
from typing import Final, Mapping, TypeAlias

import sentry_sdk

from sophie_bot.config import CONFIG

MetricAttributeValue: TypeAlias = str | bool | float | int
MetricAttributes: TypeAlias = Mapping[str, MetricAttributeValue | None]
GaugeKey: TypeAlias = tuple[str, tuple[tuple[str, MetricAttributeValue], ...]]

_GAUGE_VALUES: dict[GaugeKey, float] = {}
_GAUGE_LOCK: Final[Lock] = Lock()


def sentry_metrics_enabled() -> bool:
    return bool(CONFIG.sentry_url) and CONFIG.sentry_enable_metrics and sentry_sdk.is_initialized()


def count_metric(name: str, value: float = 1, attributes: MetricAttributes | None = None) -> None:
    if not sentry_metrics_enabled():
        return

    sentry_sdk.metrics.count(name, value, attributes=_normalize_attributes(attributes))


def distribution_metric(
    name: str,
    value: float,
    attributes: MetricAttributes | None = None,
    unit: str | None = None,
) -> None:
    if not sentry_metrics_enabled():
        return

    sentry_sdk.metrics.distribution(name, value, attributes=_normalize_attributes(attributes), unit=unit)


def set_gauge_metric(
    name: str,
    value: float,
    attributes: MetricAttributes | None = None,
    unit: str | None = None,
) -> None:
    if not sentry_metrics_enabled():
        return

    normalized_attributes = _normalize_attributes(attributes)
    _set_gauge_state(name, value, normalized_attributes)
    sentry_sdk.metrics.gauge(name, value, attributes=normalized_attributes, unit=unit)


def change_gauge_metric(
    name: str,
    delta: float,
    attributes: MetricAttributes | None = None,
    unit: str | None = None,
) -> None:
    if not sentry_metrics_enabled():
        return

    normalized_attributes = _normalize_attributes(attributes)
    next_value = _change_gauge_state(name, delta, normalized_attributes)
    sentry_sdk.metrics.gauge(name, next_value, attributes=normalized_attributes, unit=unit)


def reset_gauge_metrics() -> None:
    with _GAUGE_LOCK:
        _GAUGE_VALUES.clear()


def _normalize_attributes(attributes: MetricAttributes | None) -> dict[str, MetricAttributeValue]:
    if not attributes:
        return {}

    return {key: value for key, value in attributes.items() if value is not None}


def _make_gauge_key(name: str, attributes: Mapping[str, MetricAttributeValue]) -> GaugeKey:
    return name, tuple(sorted(attributes.items()))


def _set_gauge_state(name: str, value: float, attributes: Mapping[str, MetricAttributeValue]) -> None:
    gauge_key = _make_gauge_key(name, attributes)
    with _GAUGE_LOCK:
        _GAUGE_VALUES[gauge_key] = value


def _change_gauge_state(name: str, delta: float, attributes: Mapping[str, MetricAttributeValue]) -> float:
    gauge_key = _make_gauge_key(name, attributes)
    with _GAUGE_LOCK:
        next_value = _GAUGE_VALUES.get(gauge_key, 0.0) + delta
        _GAUGE_VALUES[gauge_key] = next_value
        return next_value
