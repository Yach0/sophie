---
name: code-review
description: Use this skill when reviewing a change set or doing a final self-review before submitting work in Sophie Bot.
---

# Code review

Run this review before final submission, especially after multi-file or behavior-changing work.

## Review goals

- Catch correctness regressions.
- Check compliance with Sophie Bot conventions.
- Confirm the change is safely testable and shippable.

## Review checklist

### 1. Correctness

- Does the change solve the requested problem without unrelated rewrites?
- Are edge cases handled where the touched code expects them?
- Are async flows, database lookups, and return types still coherent?

### 2. Project conventions

- Imports are top-level only.
- Functions are typed.
- User-facing text goes through i18n.
- STFU is used for structured formatting.
- ASS is used for bot argument parsing.
- No broad `except Exception` blocks were added without a strong reason.

### 3. Sophie-specific pitfalls

- `chat_tid` and `chat_iid` are not mixed.
- `Link[ChatModel]` queries use database IDs.
- New medium/high-risk behavior is behind a feature flag.
- Handler-side feature gating uses `FeatureFlagFilter` rather than inline disabled replies.

### 4. Tests and verification

- New handlers have E2E coverage.
- Existing tests that should change were updated, not ignored.
- `make commit` was run and its output was reviewed.

### 5. Documentation and generated artifacts

- Docs were updated when workflow or behavior changed.
- Generated outputs changed only when expected (`openapi.json`, wiki output, locale artifacts, etc.).

## Useful references

- `AGENTS.md`
- `.agents/skills/feature-flags/SKILL.md`
- `.agents/skills/e2e-testing/SKILL.md`
- `sophie_bot/utils/feature_flags.py`