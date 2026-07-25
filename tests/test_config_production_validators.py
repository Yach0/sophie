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
    "mode": "rest",
    "environment": "production",
    "api_jwt_secret": "a-real-generated-secret",
    "api_operator_token": "a-real-operator-token",
    "api_cors_origins": ["https://sophie.example"],
    "owner_id": 483808054,
}

# The exact strings deploy/templates/*.env.j2 render, per service and role. A template change that invents
# a new environment or moves a service to MODE=rest should break these.
PRODUCTION_DEPLOYS: dict[str, dict[str, str]] = {
    "rest": {"mode": "rest", "environment": "production"},
    "beta": {"mode": "bot", "environment": "production-beta"},
    "stable": {"mode": "bot", "environment": "production-stable"},
    "scheduler": {"mode": "scheduler", "environment": "production-beta"},
}

STAGING_DEPLOYS: dict[str, dict[str, str]] = {
    "rest": {"mode": "rest", "environment": "staging"},
    "beta": {"mode": "bot", "environment": "staging-beta"},
    "stable": {"mode": "bot", "environment": "staging-stable"},
    "scheduler": {"mode": "scheduler", "environment": "staging-beta"},
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
    """The exact reproduction of the original bug: this used to construct successfully."""
    with pytest.raises(ValidationError):
        build_config(mode="rest", environment="production")


def test_production_rejects_explicit_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="api_jwt_secret must be changed in production"):
        build_config(**PRODUCTION_SAFE | {"api_jwt_secret": "change_me_in_production"})


def test_production_rejects_explicit_test_operator_token() -> None:
    with pytest.raises(ValidationError, match="api_operator_token must be changed in production"):
        build_config(**PRODUCTION_SAFE | {"api_operator_token": "test"})


def test_production_rejects_wildcard_among_other_cors_origins() -> None:
    with pytest.raises(ValidationError, match="api_cors_origins must not contain"):
        build_config(**PRODUCTION_SAFE | {"api_cors_origins": ["https://sophie.example", "*"]})


def test_production_rejects_cors_origin_without_scheme() -> None:
    """The real prod misconfiguration: a bare hostname never matches a browser's Origin header."""
    with pytest.raises(ValidationError, match="api_cors_origins entries must include a scheme"):
        build_config(**PRODUCTION_SAFE | {"api_cors_origins": ["sophie-app.orangefox.tech"]})


def test_production_rejects_schemeless_cors_origin_among_valid_ones() -> None:
    with pytest.raises(ValidationError, match="sophie-app.orangefox.tech"):
        build_config(
            **PRODUCTION_SAFE | {"api_cors_origins": ["https://sophie.example", "sophie-app.orangefox.tech"]}
        )


def test_production_accepts_http_and_https_cors_origins() -> None:
    config = build_config(**PRODUCTION_SAFE | {"api_cors_origins": ["https://sophie.example", "http://localhost:5173"]})

    assert config.api_cors_origins == ["https://sophie.example", "http://localhost:5173"]


def test_production_rejects_unset_owner_id() -> None:
    """Operator login mints its token from the owner, so an unset owner_id 500s at call time."""
    unset_owner = {key: value for key, value in PRODUCTION_SAFE.items() if key != "owner_id"}

    with pytest.raises(ValidationError, match="owner_id must be set in production"):
        build_config(**unset_owner)


def test_properly_configured_production_config_is_accepted() -> None:
    config = build_config(**PRODUCTION_SAFE)

    assert config.api_jwt_secret == "a-real-generated-secret"
    assert config.api_operator_token == "a-real-operator-token"
    assert config.api_cors_origins == ["https://sophie.example"]


@pytest.mark.parametrize("environment", ["production", "production-beta", "production-stable", "production-foo"])
def test_every_production_environment_is_guarded_in_rest_mode(environment: str) -> None:
    """Any production-* flavour serving the API is guarded, not just the exact string "production"."""
    with pytest.raises(ValidationError, match="api_jwt_secret must be changed in production"):
        build_config(**PRODUCTION_SAFE | {"environment": environment, "api_jwt_secret": "change_me_in_production"})


@pytest.mark.parametrize("environment", ["development", "staging", "staging-beta", "staging-stable"])
def test_non_production_environments_keep_insecure_defaults(environment: str) -> None:
    config = build_config(mode="rest", environment=environment)

    assert config.api_jwt_secret == "change_me_in_production"


@pytest.mark.parametrize("service", sorted(PRODUCTION_DEPLOYS))
def test_real_production_deploy_templates_boot(service: str) -> None:
    """beta/stable/scheduler provision no API_* vars, so the guard must not fail their boot.

    rest.env.j2 does provision all three, so it is checked with the values that template supplies.
    """
    overrides: dict[str, Any] = dict(PRODUCTION_DEPLOYS[service])
    if service == "rest":
        overrides |= {
            "api_jwt_secret": "a-real-generated-secret",
            "api_operator_token": "a-real-operator-token",
            "api_cors_origins": ["https://sophie.example"],
            "owner_id": 483808054,
        }

    config = build_config(**overrides)

    assert config.is_production is True


@pytest.mark.parametrize("service", sorted(STAGING_DEPLOYS))
def test_real_staging_deploy_templates_are_not_production(service: str) -> None:
    config = build_config(**STAGING_DEPLOYS[service])

    assert config.is_production is False


def test_only_rest_mode_is_guarded_against_insecure_api_defaults() -> None:
    """A production bot/scheduler never reads these settings and never sets them; it must still boot."""
    for mode in ("bot", "scheduler", "nostart"):
        config = build_config(mode=mode, environment="production")

        assert config.api_jwt_secret == "change_me_in_production"
        assert config.serves_rest_api is False


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
