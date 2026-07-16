from __future__ import annotations

from datetime import timedelta

from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.modules.filters.utils_.action_duration import resolve_action_duration

FLOOD_COUNT_KEY = "antiflood:count:{chat_id}:{user_id}"
FLOOD_STATE_KEY = "antiflood:state:{chat_id}"
FLOOD_WINDOW_SECONDS = 30

DEFAULT_ACTION_NAME = "mute_user"
DEFAULT_MUTE_DURATION = timedelta(minutes=30)


def get_action_name(settings: AntifloodModel) -> str:
    if settings.actions:
        return settings.actions[0].name
    return DEFAULT_ACTION_NAME


def get_action_duration(settings: AntifloodModel) -> timedelta | None:
    if not settings.actions:
        return DEFAULT_MUTE_DURATION

    action = settings.actions[0]
    return resolve_action_duration(action.name, action.data)
