from __future__ import annotations

from pydantic import BaseModel, Field


class OpTaskAIResult(BaseModel):
    """Structured AI output for /op_task — GitLab ticket generation."""

    title: str = Field(description="A concise ticket title summarizing the issue or feature request.")
    description: str = Field(
        description="A detailed markdown description for the GitLab issue, including context from chat history, "
        "the operator notes, and any relevant replied message content."
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Suggested labels for the GitLab issue (e.g. bug, feature, enhancement).",
    )
