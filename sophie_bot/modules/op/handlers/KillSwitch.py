from __future__ import annotations

from typing import Any, TypeGuard

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import BooleanArg, OptionalArg, WordArg
from ass_tg.types.base_abc import ArgFabric

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.utils.feature_flags import FEATURE_FLAGS, FeatureType, is_enabled, list_all, set_enabled
from sophie_bot.utils.handlers import SophieMessageHandler


def _is_feature_type(feature: str) -> TypeGuard[FeatureType]:
    return feature in FEATURE_FLAGS


class KillSwitchHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_killswitch"), IsOP(True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        # feature and value are both optional to allow listing when none provided
        return {
            "feature": OptionalArg(WordArg("feature")),
            "value": OptionalArg(BooleanArg("value")),
        }

    async def handle(self) -> Any:
        feature: str | None = self.data.get("feature")
        value: bool | None = self.data.get("value")

        if not feature and value is None:
            # List all
            states = await list_all()
            lines = [f"{feature_name}: {str(enabled).lower()}" for feature_name, enabled in states.items()]
            return await self.event.reply("\n".join(lines))

        if not feature or value is None:
            allowed = ", ".join(FEATURE_FLAGS)
            return await self.event.reply(f"Usage: /op_killswitch <feature> <true|false>\nAllowed features: {allowed}")

        if not _is_feature_type(feature):
            allowed = ", ".join(FEATURE_FLAGS)
            return await self.event.reply(f"Unknown feature '{feature}'. Allowed: {allowed}")

        await set_enabled(feature, value)
        # Read back for confirmation from the runtime backend.
        current = await is_enabled(feature)
        return await self.event.reply(f"{feature}: {str(current).lower()}")
