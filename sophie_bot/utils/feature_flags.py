from __future__ import annotations

from collections.abc import Awaitable
from typing import Final, Literal, TypedDict, cast

from sentry_sdk import feature_flags as sentry_feature_flags

from sophie_bot.services.redis import aredis

# Public types
FeatureType = Literal[
    "ai_chatbot",
    "ai_translations",
    "ai_moderation",
    "ai_moderation_reasons",
    "ai_filters",
    "ai_chat_summaries",
    "ai_system_prompt_summaries",
    "ai_provider_zai",
    "filters",
    "antiflood",
    "locks",
    "filters_rest_api",
    "notes_rest_api",
    "warns_rest_api",
    "rules_rest_api",
    "locks_rest_api",
    "disabling_rest_api",
    "logging_rest_api",
    "ai_moderator_rest_api",
    "antiflood_rest_api",
    "welcomecaptcha",
    "welcomecaptcha_autokick",
    "new_feds_newfed",
    "new_feds_joinfed",
    "new_feds_leavefed",
    "new_feds_finfo",
    "new_feds_fban",
    "new_feds_funban",
    "new_feds_fbanlist",
    "new_feds_fcheck",
    "new_feds_transferfed",
    "new_feds_accepttransfer",
    "new_feds_setlog",
    "new_feds_unsetlog",
    "new_feds_fsub",
    "new_feds_funsub",
    "new_feds_import",
    "new_feds_frename",
    "new_feds_fdelete",
    "new_feds_fchats",
    "new_feds_fpromote",
    "new_feds_fdemote",
    "new_feds_fadmins",
    "feds_rest_api",
    "new_feds_fban_lazy",
    "new_feds",
]


class FeatureStates(TypedDict):
    ai_chatbot: bool
    ai_translations: bool
    ai_moderation: bool
    ai_moderation_reasons: bool
    ai_filters: bool
    ai_chat_summaries: bool
    ai_system_prompt_summaries: bool
    ai_provider_zai: bool
    filters: bool
    antiflood: bool
    locks: bool
    filters_rest_api: bool
    notes_rest_api: bool
    warns_rest_api: bool
    rules_rest_api: bool
    locks_rest_api: bool
    disabling_rest_api: bool
    logging_rest_api: bool
    ai_moderator_rest_api: bool
    antiflood_rest_api: bool
    welcomecaptcha: bool
    welcomecaptcha_autokick: bool
    new_feds_newfed: bool
    new_feds_joinfed: bool
    new_feds_leavefed: bool
    new_feds_finfo: bool
    new_feds_fban: bool
    new_feds_funban: bool
    new_feds_fbanlist: bool
    new_feds_fcheck: bool
    new_feds_transferfed: bool
    new_feds_accepttransfer: bool
    new_feds_setlog: bool
    new_feds_unsetlog: bool
    new_feds_fsub: bool
    new_feds_funsub: bool
    new_feds_import: bool
    new_feds_frename: bool
    new_feds_fdelete: bool
    new_feds_fchats: bool
    new_feds_fpromote: bool
    new_feds_fdemote: bool
    new_feds_fadmins: bool
    feds_rest_api: bool
    new_feds_fban_lazy: bool
    new_feds: bool


FEATURE_FLAGS: Final[tuple[FeatureType, ...]] = (
    "ai_chatbot",
    "ai_translations",
    "ai_moderation",
    "ai_moderation_reasons",
    "ai_filters",
    "ai_chat_summaries",
    "ai_system_prompt_summaries",
    "ai_provider_zai",
    "filters",
    "antiflood",
    "locks",
    "filters_rest_api",
    "notes_rest_api",
    "warns_rest_api",
    "rules_rest_api",
    "locks_rest_api",
    "disabling_rest_api",
    "logging_rest_api",
    "ai_moderator_rest_api",
    "antiflood_rest_api",
    "welcomecaptcha",
    "welcomecaptcha_autokick",
    "new_feds_newfed",
    "new_feds_joinfed",
    "new_feds_leavefed",
    "new_feds_finfo",
    "new_feds_fban",
    "new_feds_funban",
    "new_feds_fbanlist",
    "new_feds_fcheck",
    "new_feds_transferfed",
    "new_feds_accepttransfer",
    "new_feds_setlog",
    "new_feds_unsetlog",
    "new_feds_fsub",
    "new_feds_funsub",
    "new_feds_import",
    "new_feds_frename",
    "new_feds_fdelete",
    "new_feds_fchats",
    "new_feds_fpromote",
    "new_feds_fdemote",
    "new_feds_fadmins",
    "feds_rest_api",
    "new_feds_fban_lazy",
    "new_feds",
)


def _default_state_map() -> FeatureStates:
    return FeatureStates(
        ai_chatbot=_DEFAULT_STATES["ai_chatbot"],
        ai_translations=_DEFAULT_STATES["ai_translations"],
        ai_moderation=_DEFAULT_STATES["ai_moderation"],
        ai_moderation_reasons=_DEFAULT_STATES["ai_moderation_reasons"],
        ai_filters=_DEFAULT_STATES["ai_filters"],
        ai_chat_summaries=_DEFAULT_STATES["ai_chat_summaries"],
        ai_system_prompt_summaries=_DEFAULT_STATES["ai_system_prompt_summaries"],
        ai_provider_zai=_DEFAULT_STATES["ai_provider_zai"],
        filters=_DEFAULT_STATES["filters"],
        antiflood=_DEFAULT_STATES["antiflood"],
        locks=_DEFAULT_STATES["locks"],
        filters_rest_api=_DEFAULT_STATES["filters_rest_api"],
        notes_rest_api=_DEFAULT_STATES["notes_rest_api"],
        warns_rest_api=_DEFAULT_STATES["warns_rest_api"],
        rules_rest_api=_DEFAULT_STATES["rules_rest_api"],
        locks_rest_api=_DEFAULT_STATES["locks_rest_api"],
        disabling_rest_api=_DEFAULT_STATES["disabling_rest_api"],
        logging_rest_api=_DEFAULT_STATES["logging_rest_api"],
        ai_moderator_rest_api=_DEFAULT_STATES["ai_moderator_rest_api"],
        antiflood_rest_api=_DEFAULT_STATES["antiflood_rest_api"],
        welcomecaptcha=_DEFAULT_STATES["welcomecaptcha"],
        welcomecaptcha_autokick=_DEFAULT_STATES["welcomecaptcha_autokick"],
        new_feds_newfed=_DEFAULT_STATES["new_feds_newfed"],
        new_feds_joinfed=_DEFAULT_STATES["new_feds_joinfed"],
        new_feds_leavefed=_DEFAULT_STATES["new_feds_leavefed"],
        new_feds_finfo=_DEFAULT_STATES["new_feds_finfo"],
        new_feds_fban=_DEFAULT_STATES["new_feds_fban"],
        new_feds_funban=_DEFAULT_STATES["new_feds_funban"],
        new_feds_fbanlist=_DEFAULT_STATES["new_feds_fbanlist"],
        new_feds_fcheck=_DEFAULT_STATES["new_feds_fcheck"],
        new_feds_transferfed=_DEFAULT_STATES["new_feds_transferfed"],
        new_feds_accepttransfer=_DEFAULT_STATES["new_feds_accepttransfer"],
        new_feds_setlog=_DEFAULT_STATES["new_feds_setlog"],
        new_feds_unsetlog=_DEFAULT_STATES["new_feds_unsetlog"],
        new_feds_fsub=_DEFAULT_STATES["new_feds_fsub"],
        new_feds_funsub=_DEFAULT_STATES["new_feds_funsub"],
        new_feds_import=_DEFAULT_STATES["new_feds_import"],
        new_feds_frename=_DEFAULT_STATES["new_feds_frename"],
        new_feds_fdelete=_DEFAULT_STATES["new_feds_fdelete"],
        new_feds_fchats=_DEFAULT_STATES["new_feds_fchats"],
        new_feds_fpromote=_DEFAULT_STATES["new_feds_fpromote"],
        new_feds_fdemote=_DEFAULT_STATES["new_feds_fdemote"],
        new_feds_fadmins=_DEFAULT_STATES["new_feds_fadmins"],
        feds_rest_api=_DEFAULT_STATES["feds_rest_api"],
        new_feds_fban_lazy=_DEFAULT_STATES["new_feds_fban_lazy"],
        new_feds=_DEFAULT_STATES["new_feds"],
    )


_DEFAULT_STATES: Final[dict[FeatureType, bool]] = {
    "ai_chatbot": True,
    "ai_translations": True,
    "ai_moderation": True,
    "ai_moderation_reasons": True,
    "ai_filters": True,
    "ai_chat_summaries": True,
    "ai_system_prompt_summaries": False,
    "ai_provider_zai": True,
    "filters": True,
    "antiflood": True,
    "locks": True,
    "filters_rest_api": False,
    "notes_rest_api": True,
    "warns_rest_api": True,
    "rules_rest_api": True,
    "locks_rest_api": True,
    "disabling_rest_api": True,
    "logging_rest_api": True,
    "ai_moderator_rest_api": True,
    "antiflood_rest_api": True,
    "welcomecaptcha": True,
    "welcomecaptcha_autokick": True,
    "new_feds_newfed": True,
    "new_feds_joinfed": True,
    "new_feds_leavefed": True,
    "new_feds_finfo": True,
    "new_feds_fban": True,
    "new_feds_funban": True,
    "new_feds_fbanlist": True,
    "new_feds_fcheck": True,
    "new_feds_transferfed": True,
    "new_feds_accepttransfer": True,
    "new_feds_setlog": True,
    "new_feds_unsetlog": True,
    "new_feds_fsub": True,
    "new_feds_funsub": True,
    "new_feds_import": True,
    "new_feds_frename": True,
    "new_feds_fdelete": True,
    "new_feds_fchats": True,
    "new_feds_fpromote": True,
    "new_feds_fdemote": True,
    "new_feds_fadmins": True,
    "feds_rest_api": False,
    "new_feds_fban_lazy": True,
    "new_feds": True,
}


_REDIS_KEY: Final[str] = "sophie:kill_switch"
_TRUE_VALUE: Final[str] = "1"
_FALSE_VALUE: Final[str] = "0"


def _parse_bool_override(value: bytes | str | None) -> bool | None:
    if value is None:
        return None
    normalized_value = value.decode() if isinstance(value, bytes) else value
    if normalized_value == _TRUE_VALUE:
        return True
    if normalized_value == _FALSE_VALUE:
        return False
    return None


async def _get_override(feature: FeatureType) -> bool | None:
    value = await cast(Awaitable[bytes | str | None], aredis.hget(_REDIS_KEY, feature))
    return _parse_bool_override(value)


async def _set_override(feature: FeatureType, enabled: bool) -> None:
    encoded_value = _TRUE_VALUE if enabled else _FALSE_VALUE
    await cast(Awaitable[int], aredis.hset(_REDIS_KEY, feature, encoded_value))


async def _get_all_overrides() -> dict[FeatureType, bool]:
    raw_overrides = await cast(Awaitable[dict[bytes | str, bytes | str]], aredis.hgetall(_REDIS_KEY))
    parsed_overrides: dict[FeatureType, bool] = {}

    for raw_feature, raw_value in raw_overrides.items():
        feature_name = raw_feature.decode() if isinstance(raw_feature, bytes) else raw_feature
        if feature_name not in FEATURE_FLAGS:
            continue

        parsed_value = _parse_bool_override(raw_value)
        if parsed_value is None:
            continue

        parsed_overrides[cast(FeatureType, feature_name)] = parsed_value

    return parsed_overrides


def _track_feature_in_sentry(feature: FeatureType, enabled: bool) -> None:
    sentry_feature_flags.add_feature_flag(feature, enabled)


async def is_enabled(feature: FeatureType) -> bool:
    override = await _get_override(feature)
    enabled = override if override is not None else _DEFAULT_STATES[feature]
    _track_feature_in_sentry(feature, enabled)
    return enabled


async def set_enabled(feature: FeatureType, enabled: bool) -> None:
    await _set_override(feature, enabled)
    _track_feature_in_sentry(feature, enabled)


async def list_all() -> FeatureStates:
    merged = _default_state_map()
    overrides = await _get_all_overrides()
    for feature in FEATURE_FLAGS:
        merged[feature] = overrides.get(feature, _DEFAULT_STATES[feature])
    return merged
