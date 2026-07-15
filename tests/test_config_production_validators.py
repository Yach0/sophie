"""Tests for the production safety checks in `Config`.

These previously lived in three `field_validator`s that never fired, because `environment` is declared
after the fields they guarded and `ValidationInfo.data` only holds fields validated so far. The
default-value paths below are the ones that were completely unprotected, so they must stay covered.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from sophie_bot.config import Config

PRODUCTION_SAFE: dict[str, Any] = {
    "environment": "production",
    "api_jwt_secret": "a-real-generated-secret",
    "api_operator_token": "a-real-operator-token",
    "api_cors_origins": ["https://sophie.example"],
}


def build_config(**overrides: Any) -> Config:
    # _env_file=None keeps a developer's local data/config.env from leaking into the assertions.
    return Config(_env_file=None, **overrides)


def test_production_rejects_default_jwt_secret() -> None:
    """The field is left unset, so the default must still be rejected."""
    unset_secret = {key: value for key, value in PRODUCTION_SAFE.items() if key != "api_jwt_secret"}

    with pytest.raises(ValidationError, match="api_jwt_secret must be changed in production"):
        build_config(**unset_secret)


def test_production_rejects_default_operator_token() -> None:
    unset_token = {key: value for key, value in PRODUCTION_SAFE.items() if key != "api_operator_token"}

    with pytest.raises(ValidationError, match="api_operator_token must be changed in production"):
        build_config(**unset_token)


def test_production_rejects_default_wildcard_cors_origins() -> None:
    unset_origins = {key: value for key, value in PRODUCTION_SAFE.items() if key != "api_cors_origins"}

    with pytest.raises(ValidationError, match="api_cors_origins must not contain"):
        build_config(**unset_origins)


def test_production_rejects_every_default_at_once() -> None:
    """The exact reproduction of the original bug: `Config(environment="production")` used to construct."""
    with pytest.raises(ValidationError):
        build_config(environment="production")


def test_production_rejects_explicit_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="api_jwt_secret must be changed in production"):
        build_config(**PRODUCTION_SAFE | {"api_jwt_secret": "change_me_in_production"})


def test_production_rejects_explicit_test_operator_token() -> None:
    with pytest.raises(ValidationError, match="api_operator_token must be changed in production"):
        build_config(**PRODUCTION_SAFE | {"api_operator_token": "test"})


def test_production_rejects_wildcard_among_other_cors_origins() -> None:
    with pytest.raises(ValidationError, match="api_cors_origins must not contain"):
        build_config(**PRODUCTION_SAFE | {"api_cors_origins": ["https://sophie.example", "*"]})


def test_properly_configured_production_config_is_accepted() -> None:
    config = build_config(**PRODUCTION_SAFE)

    assert config.api_jwt_secret == "a-real-generated-secret"
    assert config.api_operator_token == "a-real-operator-token"
    assert config.api_cors_origins == ["https://sophie.example"]


@pytest.mark.parametrize("environment", ["development", "staging", "production-beta", "production-stable"])
def test_non_production_environments_keep_insecure_defaults(environment: str) -> None:
    """Only `environment == "production"` is guarded; every other deployment keeps working as before."""
    config = build_config(environment=environment)

    assert config.api_jwt_secret == "change_me_in_production"
    assert config.api_operator_token == "test"
    assert config.api_cors_origins == ["*"]


def test_default_environment_is_not_production() -> None:
    """CI and local checkouts never set ENVIRONMENT, so the default must not trip the guard on import."""
    assert Config.model_fields["environment"].default == "development"


def test_owner_id_is_added_to_operators_when_operators_unset() -> None:
    """validate_operators reads info.data["owner_id"]; owner_id is declared first, so this works."""
    config = build_config(owner_id=1234)

    assert config.operators == [1234]


def test_owner_id_is_appended_to_explicit_operators() -> None:
    config = build_config(owner_id=1234, operators=[999])

    assert config.operators == [999, 1234]
