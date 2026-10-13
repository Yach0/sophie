---
name: issue-fixer
description: Use when diagnosing and fixing bugs, errors, or unexpected behavior through root-cause analysis.
---

# Issue Fixer

## Core Principle

Fix the root cause where invalid data originates, never mask symptoms at the crash site.

## Anti-Patterns

- **Silent Exception Swallowing:** Wrapping failures in empty `try...except` blocks.
- **Defensive Defaulting:** Indiscriminately adding `.get("key", default)` or `if var is None:` guards when the upstream contract mandates presence.
- **Symptom Patching:** Changing the crash point instead of correcting the producer.

## Diagnostic Workflow

1. **Trace Upstream:** Identify the exact failing variable and trace backwards to where state is initialized or transformed.
2. **Locate Divergence:** Find the earliest point where state diverges from contract (missing payload key, incomplete query, dropped field).
3. **Fix at Source:** Correct data construction, query filters, or validation at the origin.
4. **Make Optionality Explicit:** If a field is genuinely optional, annotate with `T | None` and handle absence explicitly at boundary entry.
5. **Regression Coverage:** Add a test covering the reproduction case before closing.
