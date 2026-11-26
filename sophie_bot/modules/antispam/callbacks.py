from __future__ import annotations

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class IncidentOperation(StrEnum):
    UNDO = "undo"
    BAN = "ban"


class AntispamIncidentCallback(CallbackData, prefix="as_inc", sep="|"):
    op: IncidentOperation
    incident_id: str
