from __future__ import annotations

import asyncio

from pymongo.errors import PyMongoError

from sophie_bot.db.models.spam_match import SpamMatchModel
from sophie_bot.modules.antispam.provider import (
    ProtectionUnavailable,
    ProtectionVerdict,
    ProviderCapability,
    ProviderOrigin,
    ThreatCategory,
)
from sophie_bot.utils.feature_flags import FeatureType
from sophie_bot.utils.logger import log
from sophie_bot.utils.spam_classifier import predict_message

SPAM_THRESHOLD = 0.5


class UssrProvider:
    source_id = "ussr"
    display_name = "USSR"
    description = "Built-in local spam text classifier"
    origin = ProviderOrigin.BUILTIN
    capabilities = ProviderCapability.CONTENT_SCAN
    feature_flag: FeatureType = "antispam_ussr"
    content_scan_feature_flag = None
    attribution_url: str | None = None

    async def check_user(self, user_tid: int) -> ProtectionVerdict:
        raise NotImplementedError

    async def scan_content(
        self, text: str, user_tid: int, *, chat_tid: int, links: tuple[str, ...] = ()
    ) -> ProtectionVerdict:
        try:
            result = await asyncio.to_thread(predict_message, text, links)
        except (OSError, ValueError) as error:
            log.debug("USSR classifier unavailable", error=str(error))
            raise ProtectionUnavailable from None

        spam_probability = result["spam_probability"]
        nsfw_probability = result["nsfw_probability"]
        matched = spam_probability > SPAM_THRESHOLD or nsfw_probability > SPAM_THRESHOLD
        if matched:
            log.info(
                "Spam detection match",
                text=text,
                spam_probability=spam_probability,
                nsfw_probability=nsfw_probability,
            )
            try:
                await SpamMatchModel(
                    text=text,
                    spam_probability=spam_probability,
                    nsfw_probability=nsfw_probability,
                    chat_tid=chat_tid,
                ).insert()
            except PyMongoError as error:
                log.warning("Could not persist USSR spam match", error=str(error))

        return ProtectionVerdict(
            self.source_id,
            matched,
            (ThreatCategory.SPAM,) if matched else (),
            risk_score=max(spam_probability, nsfw_probability),
            raw_metadata={
                "spam_probability": spam_probability,
                "nsfw_probability": nsfw_probability,
            },
        )

    async def sync_bulk(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None
