---
name: feature-flags
description: Use when adding new features, external integrations, progressive rollouts, or large refactors needing flags.
---

# Feature Flags

## Rules

- New user-facing capabilities, external services, or high-risk refactors must ship behind a feature flag.
- Handlers must be gated with `FeatureFlagFilter`, never inline `is_enabled(...)` checks.
- Overrides persist in MongoDB and cache in Redis.
- Registration requires two edits:
  1. Add identifier to `FeatureType` literal union.
  2. Add default specification to `_FEATURE_DEFINITIONS`.

## Resolution Hierarchy

`Chat Override` -> `Global Override` -> `Rollout Cohort` -> `Default`

## Core API

All mutating and resolution calls require keyword-only `redis`:

```python
is_enabled(feature, chat_tid=None, *, redis) -> bool
get_value(feature, chat_tid=None, *, redis) -> Any
set_value(feature, value, *, redis) -> None
set_enabled(feature, enabled, *, redis) -> None
get_chat_override(feature, chat_tid, *, redis) -> Any
set_chat_override(feature, chat_tid, value, *, redis) -> None
delete_chat_override(feature, chat_tid, *, redis) -> None
```

## Progressive Rollouts

- Rollouts deterministically target chat cohorts (0-100%).
- Timed rollouts linearly scale from starting percentage to 100% across specified days.
- Cohort assignments persist as per-chat overrides to ensure sticky behavior.
