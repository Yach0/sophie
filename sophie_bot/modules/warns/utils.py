from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from aiogram.types import Message
from beanie import PydanticObjectId
from pydantic import TypeAdapter, ValidationError

from sophie_bot.db.models.filters import FilterActionType
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.utils_.action_config_wizard.helpers import convert_action_data_to_model
from sophie_bot.modules.restrictions.utils.restrictions import ban_user, kick_user, mute_user
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


def _action_duration_seconds(action_data: dict[str, Any]) -> Optional[float]:
    raw_duration = action_data.get("duration")
    if isinstance(raw_duration, (int, float)):
        return float(raw_duration)

    mute_duration = action_data.get("mute_duration")
    if isinstance(mute_duration, (int, float)):
        return float(mute_duration)
    if isinstance(mute_duration, str):
        try:
            ta = TypeAdapter(timedelta)
            td = ta.validate_python(mute_duration)
            return td.total_seconds()
        except (ValidationError, ValueError):
            pass

    ban_duration = action_data.get("ban_duration")
    if isinstance(ban_duration, (int, float)):
        return float(ban_duration)
    if isinstance(ban_duration, str):
        try:
            ta = TypeAdapter(timedelta)
            td = ta.validate_python(ban_duration)
            return td.total_seconds()
        except (ValidationError, ValueError):
            pass

    return None


async def _execute_restriction_action(
    action_name: str,
    action_data: dict[str, Any],
    chat_tid: int,
    user_tid: int,
) -> Optional[str]:
    duration_seconds = _action_duration_seconds(action_data)
    duration = timedelta(seconds=duration_seconds) if duration_seconds is not None else None

    if action_name == "ban_user":
        if await ban_user(chat_tid, user_tid, until_date=duration):
            return _("banned")
        return None

    if action_name == "kick_user":
        if await kick_user(chat_tid, user_tid):
            return _("kicked")
        return None

    if action_name in {"mute_user", "tmute_user"}:
        if await mute_user(chat_tid, user_tid, until_date=duration):
            return _("muted")
        return None

    return None


async def _execute_warn_actions(
    actions: list[FilterActionType],
    chat: ChatModel,
    user: ChatModel,
    admin: ChatModel,
    *,
    reason: Optional[str],
    trigger_message: Optional[Message],
    action_context: Optional[dict[str, Any]],
) -> Optional[str]:
    punishment: Optional[str] = None

    for action in actions:
        action_data = action.data if isinstance(action.data, dict) else {}

        restriction_result = await _execute_restriction_action(action.name, action_data, chat.tid, user.tid)
        if restriction_result and punishment is None:
            punishment = restriction_result
            continue

        if action.name == "warn_user":
            log.warning("Skipping nested warn action to avoid recursion", action_name=action.name, chat_tid=chat.tid)
            continue

        action_item = ALL_MODERN_ACTIONS.get(action.name)
        if not action_item or not action_item.allow_warns:
            continue

        if trigger_message is None:
            log.debug(
                "Skipping warn action because trigger message is missing",
                action_name=action.name,
                chat_tid=chat.tid,
            )
            continue

        runtime_data: dict[str, Any] = dict(action_context or {})
        runtime_data.setdefault("chat_db", chat)
        runtime_data.setdefault("user_db", admin)
        if reason is not None:
            runtime_data.setdefault("warn_reason", reason)

        filter_data = convert_action_data_to_model(action_item, action_data)
        await action_item.handle(trigger_message, runtime_data, filter_data)

    return punishment


async def warn_user(
    chat: ChatModel,
    user: ChatModel,
    admin: ChatModel,
    reason: Optional[str] = None,
    *,
    trigger_message: Optional[Message] = None,
    action_context: Optional[dict[str, Any]] = None,
) -> tuple[int, int, Optional[str], Optional[WarnModel]]:
    """
    Warns a user in a chat.
    Returns: (current_warns, max_warns, punishment_action_if_any, warn_model)
    """
    settings = await WarnSettingsModel.get_or_create(chat.iid)

    # Create warn record
    warn = WarnModel(chat=chat.iid, user=user.iid, admin=admin.iid, reason=reason)
    await warn.save()

    # Check counts
    current_warns = await WarnModel.count_user_warns(chat.iid, user.iid)
    max_warns = settings.max_warns

    punishment = None

    await _execute_warn_actions(
        settings.on_each_warn_actions,
        chat,
        user,
        admin,
        reason=reason,
        trigger_message=trigger_message,
        action_context=action_context,
    )

    if current_warns >= max_warns:
        await WarnModel.find(WarnModel.chat.id == chat.iid, WarnModel.user.id == user.iid).delete()

        max_actions = settings.on_max_warn_actions
        if not max_actions and settings.actions:
            max_actions = settings.actions

        if not max_actions:
            if await ban_user(chat.tid, user.tid):
                punishment = _("banned")
        else:
            punishment = await _execute_warn_actions(
                max_actions,
                chat,
                user,
                admin,
                reason=reason,
                trigger_message=trigger_message,
                action_context=action_context,
            )

    return current_warns, max_warns, punishment, warn


async def delete_warn(warn_iid: PydanticObjectId) -> bool:
    """Deletes a warn record by its internal ID."""
    warn = await WarnModel.get(warn_iid)
    if not warn:
        return False
    await warn.delete()
    return True
