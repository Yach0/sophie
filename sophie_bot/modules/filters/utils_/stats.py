"""Noncritical Redis-only filter counters; no backfill or persistence guarantee."""

from __future__ import annotations

from dataclasses import dataclass

from beanie import PydanticObjectId
from redis.asyncio import Redis
from redis.exceptions import RedisError

from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.utils.logger import log


@dataclass(frozen=True, slots=True)
class FilterStats:
    counts: dict[str, int]
    labels: dict[str, str]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _stats_key(chat_iid: PydanticObjectId) -> str:
    return f"filter_stats:{chat_iid}"


async def record_filter_action(chat_iid: PydanticObjectId, filter_item: FiltersModel, *, redis: Redis) -> None:
    """Increment by stable filter document ID, without interrupting enforcement on Redis errors."""
    filter_id = str(filter_item.id)
    key = _stats_key(chat_iid)
    try:
        async with redis.pipeline(transaction=True) as pipeline:
            pipeline.hincrby(key, f"count:{filter_id}", 1)
            pipeline.hset(key, f"label:{filter_id}", filter_item.handler)
            await pipeline.execute()
    except RedisError as error:
        log.warning("Could not record noncritical filter statistics", chat_iid=str(chat_iid), error=str(error))


async def get_filter_stats(chat_iid: PydanticObjectId, *, redis: Redis) -> FilterStats | None:
    """Read one chat's snapshot, including identities whose filters have since been deleted."""
    values = await redis.hgetall(_stats_key(chat_iid))
    if not values:
        return None
    decoded = {
        key.decode() if isinstance(key, bytes) else key: value.decode() if isinstance(value, bytes) else value
        for key, value in values.items()
    }
    return FilterStats(
        counts={key.removeprefix("count:"): int(value) for key, value in decoded.items() if key.startswith("count:")},
        labels={key.removeprefix("label:"): value for key, value in decoded.items() if key.startswith("label:")},
    )
