from __future__ import annotations

import asyncio
import csv
from hashlib import sha256
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

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

CHECK_URL = "https://api.cas.chat/check"
MIRROR_TTL = 86400
STAGING_TTL = 3600
CSV_BATCH_SIZE = 5000


class CasRecord(BaseModel):
    offenses: int | None = None
    reason: str | None = None


class CasResponse(BaseModel):
    ok: bool
    result: CasRecord | None = None
    error_code: int | None = None
    description: str | None = None


class CasProvider:
    source_id = "cas"
    display_name = "Combot Anti-Spam"
    description = "Telegram spam reports from Combot"
    origin = ProviderOrigin.THIRD_PARTY
    capabilities = ProviderCapability.USER_LOOKUP | ProviderCapability.BULK_SYNC
    feature_flag: FeatureType = "antispam_cas"
    content_scan_feature_flag = None
    attribution_url = "https://cas.chat"

    def __init__(
        self,
        redis: Redis,
        *,
        export_url: str | None,
        api_key: str | None = None,
        lookup_fallback: bool = True,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.redis = redis
        self.export_url = export_url
        self.api_key = api_key
        self.lookup_fallback = lookup_fallback
        self.client = client or httpx2.AsyncClient(timeout=2.0)
        mirror_id = sha256(str(export_url).encode()).hexdigest()[:16]
        self.mirror_key = f"antispam:cas:{mirror_id}:active"

    async def close(self) -> None:
        await self.client.aclose()

    async def scan_content(
        self, text: str, user_tid: int, *, chat_tid: int, links: tuple[str, ...] = ()
    ) -> ProtectionVerdict:
        raise NotImplementedError

    async def _mirror_contains(self, user_tid: int) -> bool | None:
        # Watch the pointer so retiring an old set cannot produce a false negative.
        for _attempt in range(2):
            try:
                async with self.redis.pipeline() as pipeline:
                    await pipeline.watch(self.mirror_key)
                    active = await pipeline.hget(self.mirror_key, "set")
                    if active is None:
                        return None
                    pipeline.multi()
                    pipeline.exists(active)
                    pipeline.sismember(active, str(user_tid))
                    exists, matched = await pipeline.execute()
                    return bool(matched) if exists else None
            except WatchError:
                continue
        return None

    def _verdict(self, user_tid: int, matched: bool, record: CasRecord | None = None) -> ProtectionVerdict:
        return ProtectionVerdict(
            self.source_id,
            matched,
            (ThreatCategory.SPAM,) if matched else (),
            reason=record.reason if record else None,
            reference_url=f"https://cas.chat/query?u={user_tid}",
            raw_metadata={"offenses": record.offenses} if record and record.offenses is not None else {},
        )

    async def _remote_check(self, user_tid: int) -> ProtectionVerdict:
        try:
            async with asyncio.timeout(2):
                response = await self.client.get(
                    CHECK_URL,
                    params={"user_id": user_tid},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            payload = CasResponse.model_validate_json(response.content)
            if (
                not payload.ok
                and response.status_code in {200, 404}
                and (
                    response.status_code == 404
                    or payload.error_code == 404
                    or (payload.description or "").rstrip(".").lower() in {"record not found", "not found"}
                )
            ):
                return self._verdict(user_tid, False)
            response.raise_for_status()
            if payload.ok and payload.result is not None:
                return self._verdict(user_tid, True, payload.result)
        except (httpx2.HTTPError, TimeoutError, ValidationError):
            log.debug("CAS lookup unavailable", source_id=self.source_id)
        raise ProtectionUnavailable

    async def check_user(self, user_tid: int) -> ProtectionVerdict:
        matched: bool | None = None
        if self.export_url is not None:
            try:
                matched = await self._mirror_contains(user_tid)
            except RedisError:
                matched = None
        if matched is False:
            return self._verdict(user_tid, False)
        if self.api_key and (matched or self.lookup_fallback):
            try:
                result = await cached_verdict(
                    self.redis, f"antispam:cas:lookup:{user_tid}", lambda: self._remote_check(user_tid)
                )
                if result.matched or matched is None:
                    return result
            except ProtectionUnavailable:
                if matched is None:
                    raise
        if matched:
            return self._verdict(user_tid, True)
        raise ProtectionUnavailable

    async def _stage_export(self, response: httpx2.Response, pending_key: str) -> None:
        # Redis has no empty sets. The marker makes an empty export distinct from an evicted key.
        batch: list[str] = [""]
        first_row = True
        async for line in response.aiter_lines():
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            row = next(csv.reader([line], strict=True))
            first_column = row[0].strip()
            if first_row and first_column.lower() in {"id", "user_id", "user_tid", "telegram_id"}:
                first_row = False
                continue
            first_row = False
            user_tid = int(first_column)
            if not 0 < user_tid < 2**52:
                raise ValueError("Invalid Telegram user ID in CAS export")
            batch.append(str(user_tid))
            if len(batch) >= CSV_BATCH_SIZE:
                await self._add_batch(pending_key, batch)
                batch.clear()
        if first_row:
            raise ValueError("Empty CAS export")
        if batch:
            await self._add_batch(pending_key, batch)

    async def _add_batch(self, pending_key: str, batch: list[str]) -> None:
        async with self.redis.pipeline() as pipeline:
            pipeline.sadd(pending_key, *batch)
            pipeline.expire(pending_key, STAGING_TTL)
            await pipeline.execute()

    async def _publish_export(
        self,
        old_key: bytes | str | None,
        pending_key: str,
        headers: httpx2.Headers,
        *,
        unchanged: bool,
    ) -> None:
        async with self.redis.pipeline() as pipeline:
            await pipeline.watch(self.mirror_key)
            if await pipeline.hget(self.mirror_key, "set") != old_key:
                return
            pipeline.multi()
            if unchanged and old_key is not None:
                pipeline.expire(old_key, MIRROR_TTL + STAGING_TTL)
            else:
                pipeline.hset(
                    self.mirror_key,
                    mapping={
                        "set": pending_key,
                        "etag": headers.get("ETag", ""),
                        "modified": headers.get("Last-Modified", ""),
                    },
                )
                pipeline.expire(pending_key, MIRROR_TTL + STAGING_TTL)
                if old_key is not None:
                    pipeline.unlink(old_key)
            pipeline.expire(self.mirror_key, MIRROR_TTL)
            await pipeline.execute()

    async def sync_bulk(self) -> None:
        if self.export_url is None:
            return
        pending_key = f"{self.mirror_key}:{uuid4().hex}"
        try:
            if await self.redis.exists(f"{self.mirror_key}:export_unavailable"):
                return
            raw = await self.redis.hgetall(self.mirror_key)
            metadata = {
                key.decode() if isinstance(key, bytes) else key: value.decode() if isinstance(value, bytes) else value
                for key, value in raw.items()
            }
            old_key = raw.get(b"set", raw.get("set"))
            if old_key is not None and not await self.redis.exists(old_key):
                metadata.clear()
            headers = {
                header: metadata[key]
                for header, key in (("If-None-Match", "etag"), ("If-Modified-Since", "modified"))
                if metadata.get(key)
            }
            async with (
                asyncio.timeout(180),
                self.client.stream("GET", self.export_url, headers=headers, timeout=15) as response,
            ):
                if response.status_code == 404:
                    await self.redis.set(f"{self.mirror_key}:export_unavailable", "1", ex=MIRROR_TTL)
                    log.warning("CAS export endpoint unavailable", status=404)
                    return
                unchanged = response.status_code == 304
                if unchanged:
                    if old_key is None:
                        return
                else:
                    response.raise_for_status()
                    await self._stage_export(response, pending_key)
                await self._publish_export(old_key, pending_key, response.headers, unchanged=unchanged)
        except WatchError:
            log.debug("CAS mirror already updated by another worker")
        except (httpx2.HTTPError, RedisError, TimeoutError, ValueError, csv.Error):
            log.warning("CAS export sync failed")
