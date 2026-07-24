from __future__ import annotations

from collections.abc import Collection

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.utils.api.auth import get_current_operator
from sophie_bot.utils.feature_flags import (
    FEATURE_FLAGS,
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
    get_value_kind,
    is_valid_value_type,
    list_chat_override_details,
    list_rollouts,
    set_chat_override,
    set_rollout,
    set_timed_rollout,
    set_value,
)
from sophie_bot.utils.feature_flags import (
    _get_all_overrides as get_global_overrides,
)

# Operator-only: these switches change the bot's behaviour globally.
router = APIRouter(
    prefix="/op/feature-flags",
    tags=["feature_flags"],
    dependencies=[Depends(get_current_operator)],
)


class FeatureFlag(BaseModel):
    name: str
    value: FeatureValue
    default: FeatureValue
    value_kind: str
    allowed_values: list[str] | None
    overridden: bool


class FeatureFlagUpdate(BaseModel):
    value: FeatureValue


class RolloutInfo(BaseModel):
    feature: str
    value: FeatureValue
    current_percentage: int
    start_percentage: int
    target_percentage: int
    duration_days: int | None
    started_at: str


class RolloutSet(BaseModel):
    value: FeatureValue
    # A timed rollout ramps to 100% over ``days``; otherwise it jumps to ``percentage`` at once.
    percentage: int | None = None
    days: int | None = None


class RolloutBump(BaseModel):
    percentage: int


class ChatOverride(BaseModel):
    chat_tid: int
    chat_title: str | None
    feature: str
    value: FeatureValue
    source: str


def _feature_or_404(feature: str) -> FeatureType:
    if feature not in FEATURE_FLAGS:
        raise HTTPException(status_code=404, detail="Unknown feature flag")
    return feature  # type: ignore[return-value]


def _coerce(feature: FeatureType, value: FeatureValue) -> FeatureValue:
    """Accept a JSON value and fit it to the flag's declared type, or reject it.

    JSON has no separate int/float, and a bool is an int in Python, so the flag's default type is
    the authority: an int flag must not accept ``true``, a float flag may take a whole number.
    """
    default = get_default_value(feature)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail="This flag expects a boolean")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(status_code=422, detail="This flag expects a number")
        return float(value)
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(status_code=422, detail="This flag expects an integer")
        return value
    # str
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="This flag expects a string")
    allowed = get_allowed_string_values(feature)
    if allowed is not None and value not in allowed:
        raise HTTPException(status_code=422, detail=f"Allowed values: {', '.join(sorted(allowed))}")
    return value


def _validated(feature: FeatureType, value: FeatureValue) -> FeatureValue:
    coerced = _coerce(feature, value)
    if not is_valid_value_type(feature, coerced):
        raise HTTPException(status_code=422, detail="Value type does not match this flag")
    return coerced


async def _describe(feature: FeatureType, overridden: Collection[str]) -> FeatureFlag:
    allowed = get_allowed_string_values(feature)
    return FeatureFlag(
        name=feature,
        value=await get_value(feature),
        default=get_default_value(feature),
        value_kind=get_value_kind(feature),
        allowed_values=sorted(allowed) if allowed is not None else None,
        overridden=feature in overridden,
    )


def _rollout_info(feature: str, rollout: FeatureRollout) -> RolloutInfo:
    return RolloutInfo(
        feature=feature,
        value=rollout["value"],
        current_percentage=get_rollout_percentage(rollout),
        start_percentage=rollout["start_percentage"],
        target_percentage=rollout["target_percentage"],
        duration_days=rollout["duration_days"],
        started_at=rollout["started_at"],
    )


# ── Global overrides ─────────────────────────────────────────────────────────


@router.get("", response_model=list[FeatureFlag])
async def list_feature_flags() -> list[FeatureFlag]:
    overridden = set(await get_global_overrides())
    return [await _describe(feature, overridden) for feature in sorted(FEATURE_FLAGS)]


@router.put("/{feature}", response_model=FeatureFlag)
async def set_feature_flag(feature: str, data: FeatureFlagUpdate) -> FeatureFlag:
    typed = _feature_or_404(feature)
    await set_value(typed, _validated(typed, data.value))
    return await _describe(typed, {typed})


@router.delete("/{feature}", response_model=FeatureFlag)
async def reset_feature_flag(feature: str) -> FeatureFlag:
    """Clear the global override, reverting the flag to its built-in default."""
    typed = _feature_or_404(feature)
    await delete_override(typed)
    return await _describe(typed, set())


# ── Progressive rollouts ─────────────────────────────────────────────────────


@router.get("/rollouts", response_model=list[RolloutInfo])
async def list_feature_rollouts() -> list[RolloutInfo]:
    rollouts = await list_rollouts()
    return [_rollout_info(feature, rollout) for feature, rollout in sorted(rollouts.items())]


@router.put("/{feature}/rollout", response_model=RolloutInfo)
async def set_feature_rollout(feature: str, data: RolloutSet) -> RolloutInfo:
    typed = _feature_or_404(feature)
    value = _validated(typed, data.value)

    if data.days is not None:
        await set_timed_rollout(typed, data.days, value)
    elif data.percentage is not None:
        await set_rollout(typed, data.percentage, value)
    else:
        raise HTTPException(status_code=422, detail="Provide either a target percentage or a number of days")

    rollout = await get_rollout(typed)
    if rollout is None:
        raise HTTPException(status_code=500, detail="Rollout was not stored")
    return _rollout_info(typed, rollout)


@router.post("/{feature}/rollout/bump", response_model=RolloutInfo)
async def bump_feature_rollout(feature: str, data: RolloutBump) -> RolloutInfo:
    typed = _feature_or_404(feature)
    try:
        rollout = await bump_rollout(typed, data.percentage)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return _rollout_info(typed, rollout)


@router.delete("/{feature}/rollout", status_code=204)
async def delete_feature_rollout(feature: str) -> None:
    await delete_rollout(_feature_or_404(feature))


# ── Per-chat overrides ───────────────────────────────────────────────────────


@router.get("/chat-overrides", response_model=list[ChatOverride])
async def list_feature_chat_overrides(chat_tid: int | None = None) -> list[ChatOverride]:
    """Every manual/rollout per-chat override, or just one chat's when ``chat_tid`` is given."""
    details = await list_chat_override_details(chat_tid)

    titles: dict[int, str | None] = {}
    for detail in details:
        tid = detail["chat_tid"]
        if tid not in titles:
            chat = await ChatModel.get_by_tid(tid)
            titles[tid] = chat.first_name_or_title if chat else None

    return [
        ChatOverride(
            chat_tid=detail["chat_tid"],
            chat_title=titles[detail["chat_tid"]],
            feature=detail["feature"],
            value=detail["value"],
            source=detail["source"],
        )
        for detail in details
    ]


@router.put("/{feature}/chat/{chat_tid}", response_model=ChatOverride)
async def set_feature_chat_override(feature: str, chat_tid: int, data: FeatureFlagUpdate) -> ChatOverride:
    typed = _feature_or_404(feature)
    value = _validated(typed, data.value)
    await set_chat_override(typed, chat_tid, value)
    chat = await ChatModel.get_by_tid(chat_tid)
    title = chat.first_name_or_title if chat else None
    return ChatOverride(chat_tid=chat_tid, chat_title=title, feature=typed, value=value, source="manual")


@router.delete("/{feature}/chat/{chat_tid}", status_code=204)
async def delete_feature_chat_override(feature: str, chat_tid: int) -> None:
    await delete_chat_override(_feature_or_404(feature), chat_tid)
