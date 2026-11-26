from __future__ import annotations

from asyncio import gather
from collections.abc import Iterator

from redis.asyncio import Redis

from sophie_bot.config import Config
from sophie_bot.modules.antispam.cas import CasProvider
from sophie_bot.modules.antispam.ofd import OfdProvider
from sophie_bot.modules.antispam.provider import AntispamProvider, ProviderCapability
from sophie_bot.modules.antispam.ussr import UssrProvider
from sophie_bot.utils.feature_flags import is_enabled


class ProviderRegistry:
    def __init__(self, config: Config, redis: Redis) -> None:
        self.redis = redis
        ussr = UssrProvider()
        cas = CasProvider(
            redis,
            export_url=str(config.antispam_cas_export_url) if config.antispam_cas_export_url else None,
            api_key=config.antispam_cas_api_key.get_secret_value() if config.antispam_cas_api_key else None,
            lookup_fallback=config.antispam_cas_lookup_fallback,
        )
        self.providers: dict[str, AntispamProvider] = {ussr.source_id: ussr, cas.source_id: cas}
        for server in config.antispam_ofd_servers:
            provider = OfdProvider(redis, server)
            self.providers[provider.source_id] = provider

    def __iter__(self) -> Iterator[AntispamProvider]:
        return iter(self.providers.values())

    async def active(self, chat_tid: int, capability: ProviderCapability | None = None) -> tuple[AntispamProvider, ...]:
        candidates = tuple(provider for provider in self if capability is None or capability in provider.capabilities)
        required = {
            provider.source_id: (
                (provider.feature_flag, provider.content_scan_feature_flag)
                if capability == ProviderCapability.CONTENT_SCAN and provider.content_scan_feature_flag is not None
                else (provider.feature_flag,)
            )
            for provider in candidates
        }
        enabled = {
            feature: await is_enabled(feature, chat_tid=chat_tid, redis=self.redis)
            for feature in dict.fromkeys(feature for flags in required.values() for feature in flags)
        }
        return tuple(provider for provider in candidates if all(enabled[flag] for flag in required[provider.source_id]))

    async def sync_bulk(self) -> None:
        # Chat overrides can enable the module independently of its global rollout.
        for provider in self:
            if ProviderCapability.BULK_SYNC in provider.capabilities and await is_enabled(
                provider.feature_flag, redis=self.redis
            ):
                await provider.sync_bulk()

    async def close(self) -> None:
        await gather(*(provider.close() for provider in self))
