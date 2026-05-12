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

- Global flags are deployment-global, not per-chat permissions.
- Per-chat overrides add chat-specific behavior on top of global defaults.
- They are rollout controls, not a security boundary.

## Non-boolean values

Feature flags support `bool | str | int | float` values, not just booleans.
- Boolean flags accept `true`/`false` (case-insensitive) or `1`/`0`
- String flags accept any text (e.g., model names like `openai/gpt-5-nano`)
- Numeric values are parsed as `int` first, then `float`

Public API for non-boolean flags:
- `get_value(feature, chat_tid=None)` → returns the resolved value (chat override → global override → default)
- `set_value(feature, value)` → sets a global override

## Per-chat overrides

Feature flags can be overridden per-chat using separate Redis keys (`sophie:kill_switch_chat:{chat_tid}`).

Public API:
- `get_chat_override(feature, chat_tid)` → per-chat override or None
- `set_chat_override(feature, chat_tid, value)` → set per-chat override
- `delete_chat_override(feature, chat_tid)` → remove per-chat override
- `list_chat_overrides(chat_tid)` → all per-chat overrides for a chat

Resolution order: chat override → global override → default.

## Model name flags

Two feature flags control AI model names at runtime:
- `ai_summary_model` (default: `"openai/gpt-5.5"`) — used in `get_chat_summary_model()` in `ai_get_provider.py`
- `ai_filter_handler_model` (default: `"openai/gpt-5-nano"`) — used via `get_filter_handler_model()` in `ai_model_factory.py`

## `/op_killswitch` command syntax

```
/op_killswitch                                    — list all global flags
/op_killswitch ^chat                              — list per-chat overrides for current chat
/op_killswitch ^chat=-1001234567890               — list per-chat overrides for specific chat
/op_killswitch <feature> <value>                  — set global flag value
/op_killswitch ^chat <feature> <value>            — set per-chat override for current chat
/op_killswitch ^chat=-1001234567890 <feature> <value> — set per-chat override for specific chat
```

The `^chat` key-value arg uses ASS `KeyValueArg` with `^` prefix. `^chat` without value means current chat.

## Useful references

- `sophie_bot/utils/feature_flags.py`
- `sophie_bot/filters/feature_flag.py`
- `tests/test_welcomesecurity_feature_flags.py`