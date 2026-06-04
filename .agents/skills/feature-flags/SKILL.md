---
name: feature-flags
description: Use this skill when adding new medium/high-risk features, external integrations, rollout controls, or large refactors that need feature flag controls.
---

# Feature flags

All new medium/high-risk features, external integrations, and large refactors must ship behind a feature flag.

## Source of truth

- Storage currently lives in Redis under legacy `sophie:kill_switch*` keys.
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

- Disable switch for risky deployed behavior: usually default `True`.
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
Per-chat override documents include a `source` field:
- `manual` for operator-set chat overrides
- `rollout` for chat assignments persisted by an automatic rollout

Public API:
- `get_chat_override(feature, chat_tid)` → per-chat override or None
- `set_chat_override(feature, chat_tid, value)` → set per-chat override
- `delete_chat_override(feature, chat_tid)` → remove per-chat override
- `list_chat_overrides(chat_tid)` → all per-chat overrides for a chat
- `list_chat_override_details(chat_tid=None)` → per-chat overrides with chat, feature, value, and source

Resolution order: chat override → global override → default.
Manual per-chat overrides use the same resolution layer as rollout-created chat assignments, so they always take precedence over future rollout checks.

## Progressive rollouts

Feature flags can roll out a value to a deterministic percentage of chats.
Rollouts are configured globally and applied only when a chat has no per-chat override and no global override.
When a chat is inside the rollout cohort, the rollout value is persisted as a per-chat override and cached so future checks keep the same value.
Timed rollouts increase linearly from their start percentage to 100% across the configured number of days.

Public API:
- `get_rollout(feature)` → rollout config or None
- `set_rollout(feature, percentage, value)` → configure a fixed 0-100 chat rollout
- `set_timed_rollout(feature, days, value)` → configure a rollout that reaches 100% after `days`
- `bump_rollout(feature, percentage)` → increase the current rollout percentage, capped at 100
- `delete_rollout(feature)` → remove rollout config
- `list_rollouts()` → all configured rollouts

## Model name flags

Two feature flags control AI model names at runtime:
- `ai_summary_model` (default: `"openai/gpt-5.5"`) — used in `get_chat_summary_model()` in `ai_get_provider.py`
- `ai_filter_handler_model` (default: `"openai/gpt-5-nano"`) — used via `get_filter_handler_model()` in `ai_model_factory.py`

## `/op_ff` command syntax

```
/op_ff                                           — list changed global flags and rollouts
/op_ff ^chat                                    — list per-chat overrides for current chat
/op_ff ^chat=-1001234567890                     — list per-chat overrides for specific chat
/op_ff ^chat_overrides                          — list all manual and rollout-created per-chat overrides
/op_ff <feature> <value>                        — set global flag value
/op_ff ^chat <feature> <value>                  — set per-chat override for current chat
/op_ff ^chat=-1001234567890 <feature> <value>   — set per-chat override for specific chat
/op_ff ^rollout=10 <feature> <value>            — roll out value to 10% of chats
/op_ff ^days=7 <feature> <value>                — linearly roll out value to 100% over 7 days
/op_ff ^rollout_bump=10 <feature>               — increase rollout by 10%, capped at 100
/op_ff ^rollout <feature>                       — show rollout for one feature
/op_ff ^rollout                                 — list configured rollouts
/op_ff ^rollout <feature> unset                 — delete rollout for one feature
```

The `^chat` key-value arg uses ASS `KeyValueArg` with `^` prefix. `^chat` without value means current chat.
The `^rollout` key-value arg accepts an integer percentage from 0 to 100.

## Useful references

- `sophie_bot/utils/feature_flags.py`
- `sophie_bot/filters/feature_flag.py`
- `tests/test_welcomesecurity_feature_flags.py`
