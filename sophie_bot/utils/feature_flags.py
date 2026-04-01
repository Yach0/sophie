from __future__ import annotations

from threading import Lock
from typing import Final, Literal, Mapping, Sequence, TypedDict, cast

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagResolutionDetails, FlagValueType
from openfeature.hook import Hook
from openfeature.provider import FeatureProvider, Metadata
from sentry_sdk import feature_flags as sentry_feature_flags

# Public types
FeatureType = Literal[
    "ai_chatbot",
    "ai_translations",
    "ai_moderation",
    "ai_moderation_reasons",
    "ai_filters",
    "ai_provider_zai",
    "filters",
    "antiflood",
    "locks",
    "filters_rest_api",
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
    ai_provider_zai: bool
    filters: bool
    antiflood: bool
    locks: bool
    filters_rest_api: bool
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
    "ai_provider_zai",
    "filters",
    "antiflood",
    "locks",
    "filters_rest_api",
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
        ai_provider_zai=_DEFAULT_STATES["ai_provider_zai"],
        filters=_DEFAULT_STATES["filters"],
        antiflood=_DEFAULT_STATES["antiflood"],
        locks=_DEFAULT_STATES["locks"],
        filters_rest_api=_DEFAULT_STATES["filters_rest_api"],
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
    "ai_provider_zai": True,
    "filters": True,
    "antiflood": True,
    "locks": True,
    "filters_rest_api": False,
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
    "new_feds_fchats": True,
    "new_feds_fpromote": True,
    "new_feds_fdemote": True,
    "new_feds_fadmins": True,
    "feds_rest_api": False,
    "new_feds_fban_lazy": True,
    "new_feds": True,
}


class OpenFeatureSentryProvider(FeatureProvider):
    def __init__(self) -> None:
        self._overrides: dict[FeatureType, bool] = {}
        self._lock: Final[Lock] = Lock()

    def get_metadata(self) -> Metadata:
        return Metadata(name="sophie-openfeature-sentry")

    def get_provider_hooks(self) -> list[Hook]:
        return []

    def initialize(self, evaluation_context: EvaluationContext) -> None:
        _ = evaluation_context

    def shutdown(self) -> None:
        return None

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[bool]:
        _ = evaluation_context
        if flag_key in FEATURE_FLAGS:
            feature = cast(FeatureType, flag_key)
            with self._lock:
                if feature in self._overrides:
                    return FlagResolutionDetails(value=self._overrides[feature], reason="STATIC")
                return FlagResolutionDetails(value=_DEFAULT_STATES[feature], reason="DEFAULT")

        return FlagResolutionDetails(value=default_value, reason="DEFAULT")

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[str]:
        _ = (flag_key, evaluation_context)
        return FlagResolutionDetails(value=default_value, reason="DEFAULT")

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[int]:
        _ = (flag_key, evaluation_context)
        return FlagResolutionDetails(value=default_value, reason="DEFAULT")

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[float]:
        _ = (flag_key, evaluation_context)
        return FlagResolutionDetails(value=default_value, reason="DEFAULT")

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: Sequence[FlagValueType] | Mapping[str, FlagValueType],
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[Sequence[FlagValueType] | Mapping[str, FlagValueType]]:
        _ = (flag_key, evaluation_context)
        return FlagResolutionDetails(value=default_value, reason="DEFAULT")

    def set_override(self, feature: FeatureType, enabled: bool) -> None:
        with self._lock:
            self._overrides[feature] = enabled


_provider: Final[OpenFeatureSentryProvider] = OpenFeatureSentryProvider()
api.set_provider(_provider)
_client = api.get_client()


def _track_feature_in_sentry(feature: FeatureType, enabled: bool) -> None:
    sentry_feature_flags.add_feature_flag(feature, enabled)


async def is_enabled(feature: FeatureType) -> bool:
    enabled = _client.get_boolean_value(feature, _DEFAULT_STATES[feature])
    _track_feature_in_sentry(feature, enabled)
    return enabled


async def set_enabled(feature: FeatureType, enabled: bool) -> None:
    _provider.set_override(feature, enabled)
    _track_feature_in_sentry(feature, enabled)


async def list_all() -> FeatureStates:
    merged = _default_state_map()
    for feature in FEATURE_FLAGS:
        merged[feature] = await is_enabled(feature)
    return merged
