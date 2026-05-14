from __future__ import annotations

from datetime import timedelta

from sophie_bot.db.models.antiflood import AntifloodModel

FLOOD_COUNT_KEY = "antiflood:count:{chat_id}:{user_id}"
FLOOD_STATE_KEY = "antiflood:state:{chat_id}"
FLOOD_WINDOW_SECONDS = 30

DEFAULT_ACTION_NAME = "mute_user"
DEFAULT_MUTE_DURATION = timedelta(minutes=30)


def get_action_name(settings: AntifloodModel) -> str:
    if settings.actions:
        return settings.actions[0].name
    if settings.action:
        action_map = {"ban": "ban_user", "kick": "kick_user", "mute": "mute_user"}
        return action_map.get(settings.action, DEFAULT_ACTION_NAME)
    return DEFAULT_ACTION_NAME


def get_action_duration(settings: AntifloodModel) -> timedelta | None:
    if settings.actions and settings.actions[0].data:
        duration = settings.actions[0].data.get("duration")
        if duration and isinstance(duration, (int, float)):
            return timedelta(seconds=duration)

    if not settings.actions and not settings.action:
        return DEFAULT_MUTE_DURATION

    return None
