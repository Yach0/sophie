---
name: issue-fixer
description: Use this skill when diagnosing and fixing bugs, errors, or unexpected behavior in Sophie Bot. Guides root-cause analysis instead of symptom-masking.
---

# Issue fixer

When an exception or unexpected behavior is reported, the goal is to fix the **root cause**, not silence the symptom.

## Core principle

> The error is the **symptom**, not the problem.

If a `KeyError`, `AttributeError`, `NoneType` error, or `IndexError` fires at a given line, that line is almost never the bug. The bug is wherever the data was supposed to be prepared — and wasn't.

## Anti-patterns to avoid

### ❌ Wrapping in try-catch and returning

```python
# WRONG: the dictionary doesn't have the key, and now you've hidden that fact
try:
    value = data["expected_key"]
except KeyError:
    return
```

This does not fix anything. It hides the real problem: whatever built `data` did not include `"expected_key"`. The next developer will waste hours finding out why a feature silently does nothing.

### ❌ Adding `.get()` with a fallback and moving on

```python
# WRONG: silently defaulting without understanding why the key is missing
value = data.get("expected_key", "")
```

This is valid only when the key is genuinely optional. If the code downstream **requires** this value, a silent fallback masks a data-integrity problem.

### ❌ Sprinkling `if x is None:` guards everywhere

```python
# WRONG: band-aid check that hides the upstream contract violation
if result is not None:
    process(result)
```

If `result` should never be `None`, find out why it is — don't paper over it.

## Correct approach: trace upstream

### 1. Reproduce and read the traceback

- Reproduce the issue locally or from logs.
- Identify the exact line, variable, and missing/invalid value.

### 2. Ask: "Where was this value supposed to come from?"

- Trace the variable backward through function calls, data pipelines, or DB queries.
- Check every transformation step between the source and the crash site.

### 3. Identify the first point of divergence

- The root cause is the **earliest** place where the data deviates from what downstream code expects.
- Common culprits:
  - A conditional branch that skips setting a required key.
  - A DB query returning no results that the caller assumed would always succeed.
  - A transformation function dropping fields.
  - A migration that didn't back-fill data.
  - An external API response changing shape.

### 4. Fix at the source

- If a key is missing from a dict, add it where the dict is constructed.
- If a query returns no results, handle the empty case at the query level with a clear outcome (error, default, or early return with logging).
- If a function contract is violated, enforce or update the contract — don't silently absorb the violation.

### 5. If the value is genuinely optional, make that explicit

- Use `Optional[T]` in type annotations.
- Document why it can be missing.
- Handle the `None` case at the **earliest** reasonable point with a clear decision (skip, default, log, or raise).

## Checklist for every bug fix

1. **Reproduced** the issue with a clear error or behavior description.
2. **Traced** the failing value back to its origin.
3. **Identified** the first point where the data went wrong.
4. **Fixed** the root cause — not the symptom line.
5. **No new broad `except` blocks** or silent `.get()` fallbacks were added unless the value is genuinely optional.
6. **Added or updated a test** that would have caught this bug before the fix.
7. **Verified** the fix by running the reproduction case again.

## When try-catch is appropriate

- Recovering from genuinely transient failures (network, timeout, rate limit).
- Catching a specific library exception to translate it into a domain error.
- Graceful degradation where the user must receive a useful message instead of a stack trace.

In every case, the handler must **log** the error and either retry, inform the user, or escalate. Never swallow silently.

## Useful references

- `sophie_bot/` — main bot code for tracing data flows.
- `.agents/skills/bot-handler-development/SKILL.md` — handler conventions.
- `.agents/skills/code-review/SKILL.md` — pre-submit review checklist.