from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ReportType(str, Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"


class OpDebugAISummary(BaseModel):
    """Structured AI output for /op_debug report summarization."""

    report_type: ReportType
    title: str = Field(description="A concise title summarizing the report.")
    summary: str = Field(description="A detailed summary of the report content.")
    severity: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Estimated severity or priority of the report.",
    )
    key_points: list[str] = Field(
        default_factory=list,
        description="Bullet-point list of key findings or action items.",
    )
    suggested_action: str = Field(
        default="",
        description="A suggested next step or action for the operator.",
    )
