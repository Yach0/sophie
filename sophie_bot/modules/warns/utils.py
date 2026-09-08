from __future__ import annotations

from dataclasses import replace
from typing import Any

from aiogram.types import Message
from beanie import PydanticObjectId

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.metrics.moderation import track_moderation_action, track_warn_threshold_reached
from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.modules.restrictions.utils.restrictions import (
    execute_restriction,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.action_registry import resolve_action_duration
from sophie_bot.shared.actions import RestrictionAction, StoredAction
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


async def _execute_restriction_action(
    action_name: str,
    action_data: dict[str, Any],
    chat_tid: int,
    user_tid: int,
    *,
    services: ApplicationServices,
) -> str | None:
    duration = resolve_action_duration(
        services.modules.actions,
        action_name,
        action_data,
    )

    action = {
        "ban_user": RestrictionAction.BAN,
        "kick_user": RestrictionAction.KICK,
        "mute_user": RestrictionAction.MUTE,
        "tmute_user": RestrictionAction.MUTE,
    }.get(action_name)
    if action is None:
        return None

    result = await execute_restriction(
        services.bot,
        action,
        chat_tid,
        user_tid,
        until_date=duration,
    )
    if not result.applied:
        return None
    return {
        RestrictionAction.BAN: _("banned"),
        RestrictionAction.KICK: _("kicked"),
        RestrictionAction.MUTE: _("muted"),
    }[action]


async def _execute_warn_actions(
    actions: list[StoredAction],
    chat: ChatModel,
    user: ChatModel,
    admin: ChatModel,
    *,
    reason: str | None,
    trigger_message: Message | None,
    action_context: dict[str, Any] | None,
    services: ApplicationServices,
) -> str | None:
    punishment: str | None = None

    for action in actions:
        action_data = action.data if isinstance(action.data, dict) else {}

        restriction_result = await _execute_restriction_action(
            action.name,
            action_data,
            chat.tid,
            user.tid,
            services=services,
        )
        if restriction_result and punishment is None:
            punishment = restriction_result
            continue

        if action.name == "warn_user":
            log.warning("Skipping nested warn action to avoid recursion", action_name=action.name, chat_tid=chat.tid)
            continue

        action_item = services.modules.action_handlers.get(action.name)
        definition = services.modules.actions.get(action.name)
        if not action_item or not definition or not definition.allow_warns:
            continue

        if trigger_message is None:
            log.debug(
                "Skipping warn action because trigger message is missing",
                action_name=action.name,
                chat_tid=chat.tid,
            )
            continue
        runtime_data: dict[str, Any] = dict(action_context or {})
        request_context = runtime_data.get("context")
        if isinstance(request_context, RequestContext):
            runtime_data["context"] = replace(
                request_context,
                event_chat=chat,
                target_chat=chat,
                actor=admin,
            )
        if reason is not None:
            runtime_data.setdefault("warn_reason", reason)

        filter_data = definition.load_data(action_data)
        await action_item.execute(trigger_message, runtime_data, filter_data)

    return punishment


async def warn_user(
    chat: ChatModel,
    user: ChatModel,
    admin: ChatModel,
    reason: str | None = None,
    *,
    trigger_message: Message | None = None,
    action_context: dict[str, Any] | None = None,
    services: ApplicationServices,
) -> tuple[int, int, str | None, WarnModel | None]:
    """
    Warns a user in a chat.
    Returns: (current_warns, max_warns, punishment_action_if_any, warn_model)
    """
    settings = await WarnSettingsModel.get_or_create(chat.iid)

    # Create warn record
    warn = WarnModel(chat=chat.iid, user=user.iid, admin=admin.iid, reason=reason)
    await warn.save()
    track_moderation_action("warn")

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
        services=services,
    )

    if current_warns >= max_warns:
        await WarnModel.find(WarnModel.chat.id == chat.iid, WarnModel.user.id == user.iid).delete()

        max_actions = settings.on_max_warn_actions

        if not max_actions:
            if (
                await execute_restriction(
                    services.bot,
                    RestrictionAction.BAN,
                    chat.tid,
                    user.tid,
                )
            ).applied:
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
                services=services,
            )

        track_warn_threshold_reached(punishment or "ban")

    return current_warns, max_warns, punishment, warn


async def delete_warn(warn_iid: PydanticObjectId, chat_iid: PydanticObjectId) -> bool:
    """Deletes a warn record by its internal ID, verifying it belongs to the given chat."""
    warn = await WarnModel.get(warn_iid)
    if not warn:
        return False
    if warn.chat.ref.id != chat_iid:
        return False
    await warn.delete()
    return True
