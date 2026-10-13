---
name: deep-source-issue-fixer
description: Use when resolving DeepSource static analysis findings, handling Beanie false positives, or evaluating method scopes.
---

# DeepSource Issue Fixer

## Critical Rule

Treat linter warnings as suggestions, not mandates. Verify call sites and runtime semantics before editing.

## Known False Positives

1. **Static Method Suggestions:**
   - DeepSource flags methods not using `self`/`cls`.
   - Never make static if part of public API, overridden in subclasses, or attached to Beanie/Pydantic models.
   - Only convert if verified as a standalone utility with no polymorphism expectations.

2. **Beanie Async Queries:**
   - DeepSource misinterprets Beanie coroutines and async iterators.
   - Never remove `await` from `Model.find_one()`, `.save()`, `.insert()`, or `.delete()`.
   - Never replace `async for doc in Model.find(...)` with synchronous loops.

## Suppression Syntax

When suppressing false positives:
```python
# deepsource: skip — Beanie async query requires await
# deepsource: skip — method overridden in subclasses, must remain bound
```
