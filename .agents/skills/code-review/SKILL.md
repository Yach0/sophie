---
name: code-review
description: Use when reviewing a change set or performing final self-review before submitting work.
---

# Code Review

## Checklist

1. **Correctness:** Solves the problem within requested scope. No unrelated refactors. Handles relevant edge cases and async flow integrity.
2. **Conventions:** Top-level imports only. Strict type annotations. User-visible text wrapped in `_` or `l_`. Formatted with STFU (`Template`, never `.format()`). Arguments parsed with ASS. Specific exception catches only.
3. **Identifiers:** Strict separation of `chat_tid` (int) and `chat_iid` (`PydanticObjectId`). `Link[ChatModel]` queries match `chat.iid`.
4. **Feature Flags:** Medium/high-risk features gated behind flags. Handlers filtered via `FeatureFlagFilter`.
5. **Verification:** E2E tests added for new handlers; regression tests updated. Run `make commit`.
