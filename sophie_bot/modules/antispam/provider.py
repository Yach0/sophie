from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Flag, StrEnum, auto
from types import MappingProxyType
from typing import Any, Protocol

from sophie_bot.utils.feature_flags import FeatureType


class ProviderCapability(Flag):
    USER_LOOKUP = auto()
    CONTENT_SCAN = auto()
    BULK_SYNC = auto()


class ProviderOrigin(StrEnum):
    BUILTIN = "builtin"
    THIRD_PARTY = "third_party"


class ThreatCategory(StrEnum):
    SPAM = "spam"
    SCAM = "scam"
    PHISHING = "phishing"
    IMPERSONATION = "impersonation"
    MALWARE = "malware"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ProtectionVerdict:
    source_id: str
    matched: bool
    categories: tuple[ThreatCategory, ...] = ()
    risk_score: float | None = None
    reason: str | None = None
    reference_url: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_metadata", MappingProxyType(dict(self.raw_metadata)))


class ProtectionUnavailable(Exception):
    """A provider could not determine a verdict."""


class AntispamProvider(Protocol):
    @property
    def source_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def origin(self) -> ProviderOrigin: ...

    @property
    def capabilities(self) -> ProviderCapability: ...

    @property
    def feature_flag(self) -> FeatureType: ...

    @property
    def content_scan_feature_flag(self) -> FeatureType | None: ...

    @property
    def attribution_url(self) -> str | None: ...

    async def check_user(self, user_tid: int) -> ProtectionVerdict: ...

    async def scan_content(self, text: str, user_tid: int, *, chat_tid: int) -> ProtectionVerdict: ...

    async def sync_bulk(self) -> None: ...

    async def close(self) -> None: ...
