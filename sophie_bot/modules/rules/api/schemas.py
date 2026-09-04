from __future__ import annotations

from pydantic import model_validator

from sophie_bot.utils.api.schemas import RestSaveable, validate_rest_rich_payload


class RulesResponse(RestSaveable):
    pass


class RulesPayload(RestSaveable):
    @model_validator(mode="after")
    def validate_rich_payload(self) -> RulesPayload:
        validate_rest_rich_payload(self)
        return self
