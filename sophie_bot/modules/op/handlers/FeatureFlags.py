from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeGuard, cast

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import BooleanArg, IntArg, KeyValueArg, KeyValuesArg, OptionalArg, TextArg, WordArg
from ass_tg.types.base_abc import ArgFabric, ParsedArg
from stfu_tg import BlockQuote, Code, Doc, Template

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.utils.feature_flags import (
    FEATURE_FLAGS,
    ChatFeatureOverride,
    FeatureRollout,
    FeatureType,
    FeatureValue,
    bump_rollout,
    delete_chat_override,
    delete_override,
    delete_rollout,
    get_allowed_string_values,
    get_default_value,
    get_rollout,
    get_rollout_percentage,
    get_value,
    is_valid_value_type,
    is_enabled,
    list_all,
    list_chat_override_details,
    list_chat_overrides,
    list_rollouts,
    parse_feature_value,
    set_chat_override,
    set_rollout,
    set_timed_rollout,
    set_value,
)
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _

_CURRENT_CHAT_SENTINEL = object()
_ROLLOUT_LIST_SENTINEL = object()
_CHAT_OPTION = "chat"
_ROLLOUT_OPTION = "rollout"
_DAYS_OPTION = "days"
_ROLLOUT_BUMP_OPTION = "rollout_bump"
_CHAT_OVERRIDES_OPTION = "chat_overrides"
_KEY_VALUE_HELP = "^chat[=<chat_id>] ^chat_overrides ^rollout=<0-100> ^days=<days> ^rollout_bump=<0-100>"
_MESSAGE_SOFT_LIMIT = 3400


class _IntKeyValue(IntArg):
    def __init__(self, *, no_value: object | None = None) -> None:
        super().__init__()
        self.default_no_value_value = no_value


def _is_feature_type(feature: str) -> TypeGuard[FeatureType]:
    return feature in FEATURE_FLAGS


def _reply_unknown_feature(event: Message, feature: str) -> Any:
    allowed = ", ".join(FEATURE_FLAGS)
    doc = Doc(
        Template(_("Unknown feature {feature}."), feature=Code(feature)),
        Template(_("Allowed features: {features}"), features=Code(allowed)),
    )
    return event.reply(doc.to_html())


def _reply_usage(event: Message) -> Any:
    allowed = ", ".join(FEATURE_FLAGS)
    doc = Doc(
        _("Usage: /op_ff [^chat[=<chat_id>]] <feature> <value>"),
        Template(_("Allowed features: {features}"), features=Code(allowed)),
    )
    return event.reply(doc.to_html())


def _stringify_value(value: FeatureValue) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _render_list_value(value: FeatureValue) -> str:
    if isinstance(value, bool):
        emoji = "✅" if value else "❌"
        return f"{_stringify_value(value)} {emoji}"

    rendered_value = _stringify_value(value)
    if len(rendered_value) > 15:
        return f"{rendered_value[:15]}..."
    return rendered_value


def _render_rollout_value(rollout: FeatureRollout) -> str:
    current_percentage = get_rollout_percentage(rollout)
    value = _stringify_value(rollout["value"])
    if rollout["duration_days"] is None:
        return f"rollout {current_percentage}% -> {value}"
    return f"rollout {current_percentage}%/{rollout['target_percentage']}% over {rollout['duration_days']}d -> {value}"


def _render_full_value(feature: FeatureType, value: FeatureValue) -> str:
    return Template(_("{feature}: {value}"), feature=Code(feature), value=Code(_stringify_value(value))).to_html()


def _is_valid_feature_value_type(feature: FeatureType, value: FeatureValue) -> bool:
    return is_valid_value_type(feature, value)


def _is_valid_feature_value(feature: FeatureType, value: FeatureValue) -> bool:
    if not _is_valid_feature_value_type(feature, value):
        return False
    if not isinstance(value, str):
        return True

    allowed_values = get_allowed_string_values(feature)
    return allowed_values is None or value in allowed_values


def _reply_invalid_feature_value_type(event: Message, feature: FeatureType, value: FeatureValue) -> Any:
    default_value = get_default_value(feature)
    return event.reply(
        Template(
            _("Invalid value for {feature}: expected {expected}, got {actual}."),
            feature=Code(feature),
            expected=Code(type(default_value).__name__),
            actual=Code(type(value).__name__),
        ).to_html()
    )


def _reply_invalid_feature_value(event: Message, feature: FeatureType, value: FeatureValue) -> Any:
    if not _is_valid_feature_value_type(feature, value):
        return _reply_invalid_feature_value_type(event, feature, value)

    return event.reply(
        Template(
            _("Invalid value for {feature}: {value}."),
            feature=Code(feature),
            value=Code(_stringify_value(value)),
        ).to_html()
    )


def _build_options_arg() -> OptionalArg:
    return OptionalArg(
        KeyValuesArg(
            KeyValueArg(_CHAT_OVERRIDES_OPTION, BooleanArg()),
            KeyValueArg(_CHAT_OPTION, _IntKeyValue(no_value=_CURRENT_CHAT_SENTINEL)),
            KeyValueArg(_ROLLOUT_BUMP_OPTION, _IntKeyValue()),
            KeyValueArg(_ROLLOUT_OPTION, _IntKeyValue(no_value=_ROLLOUT_LIST_SENTINEL)),
            KeyValueArg(_DAYS_OPTION, _IntKeyValue()),
        )
    )


def _extract_option_value(options: object, option: str) -> object | None:
    if not isinstance(options, Mapping):
        return None
    option_values = cast(Mapping[str, object], options)
    parsed_value = option_values.get(option)
    if parsed_value is None:
        return None
    if isinstance(parsed_value, ParsedArg):
        return parsed_value.get_value()
    return parsed_value


def _extract_chat_tid(chat_value: object, current_chat_tid: int) -> int | None:
    if chat_value is None:
        return None
    parsed_value = getattr(chat_value, "value", chat_value)
    if parsed_value is _CURRENT_CHAT_SENTINEL:
        return current_chat_tid
    chat_tid = cast(int, parsed_value)
    return chat_tid


def _extract_rollout_percentage(rollout_value: object) -> int | object | None:
    if rollout_value is None:
        return None
    parsed_value = getattr(rollout_value, "value", rollout_value)
    if parsed_value is _ROLLOUT_LIST_SENTINEL:
        return _ROLLOUT_LIST_SENTINEL
    percentage = cast(int, parsed_value)
    return percentage


def _extract_optional_int(value: object) -> int | None:
    if value is None:
        return None
    parsed_value = getattr(value, "value", value)
    return cast(int, parsed_value)


def _is_valid_percentage(value: int | object | None) -> bool:
    return not isinstance(value, int) or 0 <= value <= 100


def _render_feature_summary(feature: FeatureType, value: FeatureValue, rollout: FeatureRollout | None) -> str:
    default_value = get_default_value(feature)
    changed_value = rollout["value"] if rollout is not None else value
    line = f"{feature}: {_stringify_value(default_value)} -> {_render_list_value(changed_value)}"
    if rollout is not None:
        line = f"{line} ({_render_rollout_value(rollout)})"
    return line


def _render_chat_override_summary(override: ChatFeatureOverride) -> str:
    value = _render_list_value(override["value"])
    source = override["source"]
    return f"{override['chat_tid']}: {override['feature']} -> {value} ({source})"


def _render_key_value_help() -> Template:
    return Template(_("Args: {args}"), args=Code(_KEY_VALUE_HELP))


def _chunk_lines(title: str, lines: list[str], empty_text: str, *, blockquote: bool) -> list[Doc]:
    pending_lines = lines or [empty_text]
    docs: list[Doc] = []
    current_lines: list[str] = []
    current_len = 0

    for line in pending_lines:
        line_cost = len(line.encode())
        if current_lines and current_len + line_cost > _MESSAGE_SOFT_LIMIT:
            body = Doc(title, *current_lines)
            docs.append(Doc(BlockQuote(body, expandable=True) if blockquote else body))
            current_lines = [line]
            current_len = line_cost
        else:
            current_lines.append(line)
            current_len += line_cost

    body = Doc(title, *current_lines)
    docs.append(Doc(BlockQuote(body, expandable=True) if blockquote else body))
    return docs


def _render_flag_list(changed_lines: list[str], all_lines: list[str], rollout_lines: list[str]) -> list[Doc]:
    if not changed_lines:
        changed_lines = [_("No changed feature flags are set.")]

    docs = _chunk_lines(
        _("Changed feature flags"), changed_lines, _("No changed feature flags are set."), blockquote=False
    )
    docs.extend(_chunk_lines(_("All feature flags"), all_lines, _("No feature flags are registered."), blockquote=True))
    docs.extend(_chunk_lines(_("Rollouts"), rollout_lines, _("No feature flag rollouts are set."), blockquote=True))
    docs.append(Doc(_render_key_value_help()))
    return docs


def _render_chat_override_list(overrides: list[ChatFeatureOverride]) -> list[Doc]:
    manual_lines = [_render_chat_override_summary(override) for override in overrides if override["source"] == "manual"]
    rollout_lines = [
        _render_chat_override_summary(override) for override in overrides if override["source"] == "rollout"
    ]
    docs = _chunk_lines(
        _("Manual per-chat overrides"),
        manual_lines,
        _("No manual per-chat overrides are set."),
        blockquote=True,
    )
    docs.extend(
        _chunk_lines(
            _("Rollout-created per-chat overrides"),
            rollout_lines,
            _("No rollout-created per-chat overrides are set."),
            blockquote=True,
        )
    )
    docs.append(Doc(_render_key_value_help()))
    return docs


class FeatureFlagsHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("op_killswitch", "op_ff")), IsOP(True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        # feature and value are both optional to allow listing when none provided
        return {
            "options": _build_options_arg(),
            "feature": OptionalArg(WordArg("feature")),
            "value": OptionalArg(TextArg("value")),
        }

    async def handle(self) -> Any:
        feature: str | None = self.data.get("feature")
        raw_value: str | None = self.data.get("value")
        options = self.data.get("options")
        chat_overrides = bool(_extract_option_value(options, _CHAT_OVERRIDES_OPTION))
        chat_arg = _extract_option_value(options, _CHAT_OPTION)
        chat_tid = _extract_chat_tid(chat_arg, self.event.chat.id)
        rollout_arg = _extract_option_value(options, _ROLLOUT_OPTION)
        rollout_percentage = _extract_rollout_percentage(rollout_arg)
        days = _extract_optional_int(_extract_option_value(options, _DAYS_OPTION))
        rollout_bump = _extract_optional_int(_extract_option_value(options, _ROLLOUT_BUMP_OPTION))

        if not _is_valid_percentage(rollout_percentage):
            return await self.event.reply(_("Usage: /op_ff ^rollout=<0-100> <feature> <value>"))
        if not _is_valid_percentage(rollout_bump):
            return await self.event.reply(_("Usage: /op_ff ^rollout_bump=<0-100> <feature>"))
        if days is not None and days <= 0:
            return await self.event.reply(_("Usage: /op_ff ^days=<days> <feature> <value>"))

        if chat_overrides:
            if feature or raw_value is not None:
                return await self.event.reply(_("Usage: /op_ff ^chat_overrides"))
            overrides = await list_chat_override_details()
            return await self._reply_docs(_render_chat_override_list(overrides))

        if rollout_percentage is not None or days is not None or rollout_bump is not None:
            return await self._handle_rollout(feature, raw_value, chat_tid, rollout_percentage, days, rollout_bump)

        if not feature and raw_value is None:
            states = await list_chat_overrides(chat_tid) if chat_tid is not None else await list_all()
            rollouts = {} if chat_tid is not None else await list_rollouts()
            # Per-chat listing intentionally treats "Changed" as explicit chat-level overrides only.
            # Global overrides that affect this chat are still visible in the expanded "All" section.
            all_states = (
                {feature_name: await get_value(feature_name, chat_tid=chat_tid) for feature_name in FEATURE_FLAGS}
                if chat_tid is not None
                else states
            )
            changed_lines = []
            for feature_name, value in states.items():
                rollout = rollouts.get(feature_name)
                if chat_tid is not None or rollout is not None or value != get_default_value(feature_name):
                    changed_lines.append(_render_feature_summary(feature_name, value, rollout))
            all_lines = [
                _render_feature_summary(feature_name, value, rollouts.get(feature_name))
                for feature_name, value in all_states.items()
            ]
            rollout_lines = [
                f"{feature_name}: {_render_rollout_value(rollout)}" for feature_name, rollout in rollouts.items()
            ]
            return await self._reply_docs(_render_flag_list(changed_lines, all_lines, rollout_lines))

        if feature and raw_value is None and _is_feature_type(feature):
            value = await get_value(feature, chat_tid=chat_tid)
            return await self.event.reply(_render_full_value(feature, value))

        if not feature or raw_value is None:
            return await _reply_usage(self.event)

        if not _is_feature_type(feature):
            return await _reply_unknown_feature(self.event, feature)

        if raw_value.strip().lower() == "unset":
            if chat_tid is not None:
                await delete_chat_override(feature, chat_tid)
                return await self.event.reply(
                    Template(
                        _("{feature} override for chat {chat} deleted."),
                        feature=Code(feature),
                        chat=chat_tid,
                    ).to_html()
                )
            await delete_override(feature)
            return await self.event.reply(
                Template(
                    _("{feature} override deleted."),
                    feature=Code(feature),
                ).to_html()
            )

        value = parse_feature_value(raw_value)
        if not _is_valid_feature_value(feature, value):
            return await _reply_invalid_feature_value(self.event, feature, value)

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

    async def _reply_docs(self, docs: list[Doc]) -> None:
        for index, doc in enumerate(docs):
            if index == 0:
                await self.event.reply(doc.to_html())
            else:
                await self.event.answer(doc.to_html())

    async def _handle_rollout(
        self,
        feature: str | None,
        raw_value: str | None,
        chat_tid: int | None,
        rollout_percentage: int | object | None,
        days: int | None,
        rollout_bump: int | None,
    ) -> Any:
        if chat_tid is not None:
            return await self.event.reply(_("Rollouts cannot be combined with per-chat overrides."))
        # A bare ^rollout uses _ROLLOUT_LIST_SENTINEL, which should count as a rollout mode selection.
        if sum(value is not None for value in (rollout_percentage, days, rollout_bump)) > 1:
            return await self.event.reply(_("Use only one rollout argument at a time."))

        if not feature:
            if days is not None:
                return await self.event.reply(_("Usage: /op_ff ^days=<days> <feature> <value>"))
            if rollout_bump is not None:
                return await self.event.reply(_("Usage: /op_ff ^rollout_bump=<0-100> <feature>"))

            rollouts = await list_rollouts()
            if not rollouts:
                return await self.event.reply(_("No feature flag rollouts are set."))
            lines = [f"{feature_name}: {_render_rollout_value(rollout)}" for feature_name, rollout in rollouts.items()]
            return await self.event.reply("\n".join(lines))

        if not _is_feature_type(feature):
            return await _reply_unknown_feature(self.event, feature)

        if raw_value is None:
            if rollout_bump is not None:
                return await self._handle_rollout_bump(feature, rollout_bump)
            if days is not None:
                return await self.event.reply(_("Usage: /op_ff ^days=<days> <feature> <value>"))

            rollout = await get_rollout(feature)
            if rollout is None:
                return await self.event.reply(
                    Template(_("No rollout is set for {feature}."), feature=Code(feature)).to_html()
                )
            return await self.event.reply(
                Template(
                    _("{feature}: {rollout}"),
                    feature=Code(feature),
                    rollout=Code(_render_rollout_value(rollout)),
                ).to_html()
            )

        if raw_value.strip().lower() == "unset":
            await delete_rollout(feature)
            return await self.event.reply(Template(_("{feature} rollout deleted."), feature=Code(feature)).to_html())

        if rollout_percentage is _ROLLOUT_LIST_SENTINEL:
            return await self.event.reply(_("Usage: /op_ff ^rollout=<0-100> <feature> <value>"))
        if rollout_bump is not None:
            return await self.event.reply(_("Usage: /op_ff ^rollout_bump=<0-100> <feature>"))

        value = parse_feature_value(raw_value)
        if not _is_valid_feature_value(feature, value):
            return await _reply_invalid_feature_value(self.event, feature, value)

        if days is not None:
            await set_timed_rollout(feature, days, value)
        else:
            percentage = cast(int, rollout_percentage)
            await set_rollout(feature, percentage, value)

        current = await get_rollout(feature)
        if current is None:
            return await self.event.reply(_("Rollout was not saved."))
        return await self.event.reply(
            Template(
                _("{feature}: {rollout}"),
                feature=Code(feature),
                rollout=Code(_render_rollout_value(current)),
            ).to_html()
        )

    async def _handle_rollout_bump(self, feature: FeatureType, percentage: int) -> Any:
        try:
            rollout = await bump_rollout(feature, percentage)
        except ValueError:
            return await self.event.reply(
                Template(_("No rollout is set for {feature}."), feature=Code(feature)).to_html()
            )

        return await self.event.reply(
            Template(
                _("{feature}: {rollout}"),
                feature=Code(feature),
                rollout=Code(_render_rollout_value(rollout)),
            ).to_html()
        )
