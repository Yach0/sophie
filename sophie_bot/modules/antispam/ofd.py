from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from hashlib import sha256
from time import time

import httpx
from pydantic import ValidationError
from pyfederation import (
    AsyncFederationClient,
    ClassificationFlag,
    ContentInput,
    FederationClientException,
    IncidentType,
    SuggestedAction,
)
from redis.asyncio import Redis
from redis.exceptions import RedisError

from sophie_bot.config import OfdServerConfig
from sophie_bot.modules.antispam.cache import cached_verdict
from sophie_bot.modules.antispam.provider import (
    ProtectionUnavailable,
    ProtectionVerdict,
    ProviderCapability,
    ProviderOrigin,
    ThreatCategory,
)
from sophie_bot.utils.feature_flags import FeatureType
from sophie_bot.utils.logger import log

ENTITY_BLOCKS = (SuggestedAction.TEMPORARILY_BLOCK_ENTITY, SuggestedAction.PERMANENTLY_BLOCK_ENTITY)


def _categories(incidents: Iterable[IncidentType], *, matched: bool) -> tuple[ThreatCategory, ...]:
    category_by_value = {category.value: category for category in ThreatCategory}
    return tuple(
        dict.fromkeys(category_by_value.get(incident.lower(), ThreatCategory.OTHER) for incident in incidents)
    ) or ((ThreatCategory.OTHER,) if matched else ())


class OfdProvider:
    description = "Open Federated Database"
    origin = ProviderOrigin.THIRD_PARTY
    capabilities = ProviderCapability.USER_LOOKUP | ProviderCapability.CONTENT_SCAN
    feature_flag: FeatureType = "antispam_ofd"
    content_scan_feature_flag: FeatureType = "antispam_ofd_content_scan"

    def __init__(self, redis: Redis, config: OfdServerConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.redis = redis
        self.source_id = f"ofd:{config.id}"
        self.display_name = config.name
        self.attribution_url = str(config.web_url or config.url)
        self.entity_host = config.entity_host
        token = config.access_token.get_secret_value() if config.access_token else None
        self.client = AsyncFederationClient(str(config.url), token, client or httpx.AsyncClient(timeout=2.0))
        scope = sha256(f"{config.url}\0{self.entity_host}".encode()).hexdigest()[:16]
        self.key_prefix = f"antispam:{self.source_id}:{scope}"

    async def close(self) -> None:
        await self.client.aclose()

    async def sync_bulk(self) -> None:
        raise NotImplementedError

    async def _open_circuit(self, delay: int) -> None:
        key = f"{self.key_prefix}:circuit"
        try:
            async with self.redis.pipeline() as pipeline:
                pipeline.set(key, "open", ex=delay, nx=True)
                pipeline.expire(key, delay, gt=True)
                await pipeline.execute()
        except RedisError:
            log.debug("Could not update anti-spam circuit", source_id=self.source_id)

    async def _request(self, lookup: Callable[[], Awaitable[ProtectionVerdict]]) -> ProtectionVerdict:
        try:
            if await self.redis.exists(f"{self.key_prefix}:circuit"):
                raise ProtectionUnavailable
        except RedisError:
            raise ProtectionUnavailable from None

        delay = 30
        try:
            async with asyncio.timeout(2):
                return await lookup()
        except FederationClientException as error:
            if error.is_retryable and error.retry_after is not None:
                delay = max(error.retry_after, 1)
            log.debug("OFD request failed", source_id=self.source_id, status=error.status_code)
        except (TimeoutError, ValidationError, httpx.HTTPError, ValueError):
            log.debug("OFD response unavailable", source_id=self.source_id)
        await self._open_circuit(delay)
        raise ProtectionUnavailable

    async def check_user(self, user_tid: int) -> ProtectionVerdict:
        async def lookup() -> ProtectionVerdict:
            try:
                result = await self.client.query_entity(f"{user_tid}@{self.entity_host}")
            except FederationClientException as error:
                if error.status_code == 404:
                    return ProtectionVerdict(self.source_id, False, reference_url=self.attribution_url)
                raise
            now = time()
            active = tuple(
                record
                for record in result.active_blacklists
                if not record.lifted and (record.expires is None or record.expires > now)
            )
            blocked = result.suggested_action in ENTITY_BLOCKS and (
                result.suggested_lift_timestamp is None or result.suggested_lift_timestamp > now
            )
            matched = bool(active) or blocked
            expirations = [record.expires for record in active if record.expires is not None]
            if blocked and result.suggested_lift_timestamp is not None:
                expirations.append(result.suggested_lift_timestamp)
            return ProtectionVerdict(
                self.source_id,
                matched,
                _categories((record.type for record in active), matched=matched),
                reference_url=self.attribution_url,
                raw_metadata={
                    "suggested_action": result.suggested_action,
                    "expires_at": min(expirations, default=None),
                },
            )

        return await cached_verdict(self.redis, f"{self.key_prefix}:user:{user_tid}", lambda: self._request(lookup))

    async def scan_content(
        self, text: str, user_tid: int, *, chat_tid: int, links: tuple[str, ...] = ()
    ) -> ProtectionVerdict:
        async def lookup() -> ProtectionVerdict:
            scan = await self.client.scan_content(
                ContentInput(text), author=f"{user_tid}@{self.entity_host}", concerns=None
            )
            classification = scan.classification.classification_flag if scan.classification else None
            matched = classification == ClassificationFlag.MALICIOUS or scan.suggested_action in (
                SuggestedAction.BLOCK_CONTENT,
                *ENTITY_BLOCKS,
            )
            return ProtectionVerdict(
                self.source_id,
                matched,
                _categories((detection.concern for detection in scan.detections), matched=matched),
                risk_score=scan.risk_score,
                reference_url=self.attribution_url,
                raw_metadata={"classification": classification, "suggested_action": scan.suggested_action},
            )

        digest = sha256(text.encode()).hexdigest()
        key = f"{self.key_prefix}:scan:{chat_tid}:{user_tid}:{digest}"
        return await cached_verdict(self.redis, key, lambda: self._request(lookup))
