from __future__ import annotations

from typing import Any, ClassVar, Literal

from beanie import Document, UpdateResponse
from beanie.odm.operators.update.general import Set
from pymongo import ASCENDING, IndexModel

FeatureFlagOverrideSource = Literal["manual", "rollout"]


class FeatureFlagOverride(Document):
    feature: str
    chat_tid: int | None = None
    value: Any
    source: FeatureFlagOverrideSource = "manual"

    class Settings:
        name = "feature_flag_overrides"
        indexes: ClassVar = [
            IndexModel([("feature", ASCENDING), ("chat_tid", ASCENDING)], unique=True),
        ]

    @staticmethod
    async def get_override(feature: str, chat_tid: int | None = None) -> FeatureFlagOverride | None:
        return await FeatureFlagOverride.find_one(
            FeatureFlagOverride.feature == feature,
            FeatureFlagOverride.chat_tid == chat_tid,
        )

    @staticmethod
    async def set_override(
        feature: str,
        value: Any,
        chat_tid: int | None = None,
        source: FeatureFlagOverrideSource = "manual",
    ) -> FeatureFlagOverride:
        return await FeatureFlagOverride.find_one(
            FeatureFlagOverride.feature == feature,
            FeatureFlagOverride.chat_tid == chat_tid,
        ).upsert(
            Set({FeatureFlagOverride.value: value, FeatureFlagOverride.source: source}),
            on_insert=FeatureFlagOverride(feature=feature, chat_tid=chat_tid, value=value, source=source),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    @staticmethod
    async def delete_override(feature: str, chat_tid: int | None = None) -> None:
        override = await FeatureFlagOverride.get_override(feature, chat_tid=chat_tid)
        if override is not None:
            await override.delete()
