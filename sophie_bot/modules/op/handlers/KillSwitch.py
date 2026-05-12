from __future__ import annotations

from typing import Any, TypeGuard, cast

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import IntArg, OptionalArg, WordArg
from ass_tg.types.base_abc import ArgFabric
from ass_tg.types.keyvalue import KeyValueArg, KeyValuesArg
from stfu_tg import Code, Doc, Template

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.utils.feature_flags import (
    FEATURE_FLAGS,
    FeatureType,
    FeatureValue,
    get_value,
    is_enabled,
    list_all,
    list_chat_overrides,
    set_chat_override,
    set_value,
)
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


_CURRENT_CHAT_SENTINEL = object()


def _is_feature_type(feature: str) -> TypeGuard[FeatureType]:
    return feature in FEATURE_FLAGS


def _parse_value(value: str) -> FeatureValue:
    normalized_value = value.lower()
    if normalized_value in {"true", "1"}:
        return True
    if normalized_value in {"false", "0"}:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _stringify_value(value: FeatureValue) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _build_chat_arg() -> KeyValuesArg:
    chat_arg = OptionalArg(IntArg("chat"))
    chat_arg.default_no_value_value = _CURRENT_CHAT_SENTINEL
    return KeyValuesArg(KeyValueArg("chat", chat_arg))


def _extract_chat_tid(chat_value: object, current_chat_tid: int) -> int | None:
    if chat_value is None:
        return None
    parsed_value = getattr(chat_value, "value", chat_value)
    if parsed_value is _CURRENT_CHAT_SENTINEL:
        return current_chat_tid
    chat_tid = cast(int, parsed_value)
    return chat_tid


class KillSwitchHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_killswitch"), IsOP(True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        # feature and value are both optional to allow listing when none provided
        return {
            "chat_args": _build_chat_arg(),
            "feature": OptionalArg(WordArg("feature")),
            "value": OptionalArg(WordArg("value")),
        }

    async def handle(self) -> Any:
        feature: str | None = self.data.get("feature")
        raw_value: str | None = self.data.get("value")
        chat_args = self.data.get("chat_args") or {}
        chat_tid = _extract_chat_tid(chat_args.get("chat"), self.event.chat.id)

        if not feature and raw_value is None:
            states = await list_chat_overrides(chat_tid) if chat_tid is not None else await list_all()
            lines = [f"{feature_name}: {_stringify_value(value)}" for feature_name, value in states.items()]
            if not lines:
                lines = [_("No per-chat overrides are set.")]
            return await self.event.reply("\n".join(lines))

        if not feature or raw_value is None:
            allowed = ", ".join(FEATURE_FLAGS)
            doc = Doc(
                _("Usage: /op_killswitch [^chat[=<chat_id>]] <feature> <value>"),
                Template(_("Allowed features: {features}"), features=Code(allowed)),
            )
            return await self.event.reply(doc.to_html())

        if not _is_feature_type(feature):
            allowed = ", ".join(FEATURE_FLAGS)
            doc = Doc(
                Template(_("Unknown feature {feature}."), feature=Code(feature)),
                Template(_("Allowed features: {features}"), features=Code(allowed)),
            )
            return await self.event.reply(doc.to_html())

        value = _parse_value(raw_value)
        if chat_tid is not None:
            await set_chat_override(feature, chat_tid, value)
            current = await get_value(feature, chat_tid=chat_tid)
            return await self.event.reply(
                Template(
                    _("{feature} for chat {chat}: {value}"),
                    feature=Code(feature),
                    chat=chat_tid,
                    value=Code(_stringify_value(current)),
                ).to_html()
            )

        await set_value(feature, value)
        # Read back for confirmation from the runtime backend.
        current = await is_enabled(feature) if isinstance(value, bool) else await get_value(feature)
        return await self.event.reply(
            Template(_("{feature}: {value}"), feature=Code(feature), value=Code(_stringify_value(current))).to_html()
        )
