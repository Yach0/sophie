---
name: feature-flags
description: Use this skill when adding new medium/high-risk features, external integrations, rollout controls, or large refactors that need a kill switch.
---

# Feature flags

All new medium/high-risk features, external integrations, and large refactors must ship behind a feature flag.

## Source of truth

- Storage lives in Redis under `sophie:kill_switch`.
- Runtime API lives in `sophie_bot/utils/feature_flags.py`.
- That file is the single source of truth for:
  - `FeatureType`
  - `FeatureStates`
  - `FEATURE_FLAGS`
  - `_default_state_map()`

## When a flag is required

- New user-facing capabilities.
- Risky moderation or behavior changes.
- New external services or schedulers.
- Large refactors that may need an instant rollback.

## Adding a new flag

Update every required location in `sophie_bot/utils/feature_flags.py`:

1. Add the literal to `FeatureType`.
2. Add the typed field to `FeatureStates`.
3. Append the flag to `FEATURE_FLAGS`.
4. Set the default in `_default_state_map()`.

Choose defaults intentionally:

- Kill-switch for risky deployed behavior: usually default `True`.
- Gradual rollout for a new area: usually default `False`.

## Handler and service usage

- Handlers must use `FeatureFlagFilter` so the handler is fully disabled when the flag is off.
- Do not send inline “feature disabled” handler replies based on `is_enabled(...)`.
- Service or utility code may use `is_enabled(...)` to select old/new behavior paths.

## Testing

- Prefer black-box tests for both enabled and disabled states.
- Use `set_enabled(...)` in tests and restore the original state afterward.
- Keep disabled behavior silent or fallback-based unless the product explicitly requires a user-facing response.

## Operational notes

- Flags are deployment-global, not per-chat permissions.
- They are rollout controls, not a security boundary.

## Useful references

- `sophie_bot/utils/feature_flags.py`
- `sophie_bot/filters/feature_flag.py`
- `tests/test_welcomesecurity_feature_flags.py`