from datetime import datetime, timezone
from beanie import Document
from pydantic import Field, ConfigDict


class MigrationState(Document):
    """Track applied migrations in database."""

    class Settings:
        name = "migration_states"
        indexes = ["name"]

    name: str = Field(..., description="Migration filename without extension")
    applied_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="When migration was applied"
    )
    version: str = Field(default="1.0", description="Migration format version")
    batch_size: int | None = Field(default=None, description="Number of documents migrated (for large collections)")
    duration_ms: int | None = Field(default=None, description="Migration duration in milliseconds")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "20240125_120000_add_user_preferences",
                "applied_at": "2024-01-25T12:00:00",
                "version": "1.0",
            }
        }
    )
