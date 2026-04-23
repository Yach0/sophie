---
name: deep-source-issue-fixer
description: Use this skill when fixing issues reported by DeepSource static analysis. Covers common false positives and Sophie-specific pitfalls.
---

# DeepSource issue fixer

DeepSource is a static analysis tool that runs on the codebase. Its suggestions are useful but frequently wrong in the context of Sophie Bot. Apply critical judgment before every fix.

## Core principle

> A DeepSource issue is a **suggestion**, not a mandate. Verify every fix against the actual runtime behavior and call sites before applying it.

## Known false positives

### 1. "Make method static" suggestions

DeepSource often flags class methods that don't reference `self` or `cls` and suggests converting them to `@staticmethod`.

**Before applying, you must check:**

- Search the entire codebase for call sites of that method.
- If **any** call site invokes it on an instance or class (e.g., `instance.method(...)` or `MyClass.method(...)`) and the method is later overridden in a subclass, making it static changes the dispatch semantics and may break polymorphism.
- If the method is part of a **public API** or a **base class interface**, it should stay as a regular method even if it doesn't use `self`, because subclasses may override it.
- If the method is a **Beanie/Pydantic model** method, keep it as a regular method — the ORM and serialization layers may rely on the method binding.

**When it is safe to apply:**

- The method is a standalone utility with no callers that reference it through `self` or the class.
- You have verified with `grep` that every call site already calls it as a free function or will continue to work after the change.

**Verification steps:**

```
1. grep the method name across the whole project
2. check every call site — is it called on self? on a class? as a free function?
3. check if any subclass overrides it
4. only then decide: staticmethod, or leave as-is
```

### 2. Beanie async patterns

DeepSource does not understand Beanie's async query API. It will frequently flag:

- `await Model.find_one(...)` — DeepSource may claim `find_one` is not a coroutine.
- `async for doc in Model.find(...)` — DeepSource may suggest removing `async` or rewriting as a regular `for` loop with `await` inside.
- `await doc.save()` / `await doc.insert()` / `await doc.delete()` — same false positive.

**Do NOT:**

- Remove `await` from Beanie query calls. They are coroutines and will return an unawaited coroutine object if you skip `await`.
- Change `async for doc in Model.find(...)` to `for doc in await Model.find(...)` or similar rewrites. `Model.find()` returns an async iterator; you must use `async for`.
- Add `await` inside a non-async `for` loop as a "fix". The correct pattern is `async for`.

**Correct Beanie patterns to preserve:**

```python
# Single document lookup — always await
doc = await Model.find_one(Model.field == value)

# Iterating results — always async for
async for doc in Model.find(Model.field == value):
    ...

# Writing — always await
await doc.save()
await doc.insert()
await doc.delete()
```

**If DeepSource flags these, suppress or ignore the issue.** Do not restructure the code to satisfy the linter.

## General approach to DeepSource issues

### Step 1: Read the issue carefully

Understand what DeepSource is actually complaining about before touching code.

### Step 2: Check if it's a known false positive

Match it against the sections above. If it falls under a known false-positive category, skip it or add an inline suppression with a comment explaining why.

### Step 3: If it seems legitimate, verify against call sites

- Use `grep` to find all usages of the symbol in question.
- Confirm the suggested change doesn't break any caller.
- Check subclass overrides and interface contracts.

### Step 4: Apply the fix only when confident

- Make the minimal change that addresses the issue.
- Run the test suite to verify nothing broke.

### Step 5: When in doubt, leave it as-is

A false negative (ignoring a minor style issue) is far cheaper than a false positive (introducing a runtime bug to satisfy a linter).

## Suppression comments

When suppressing a DeepSource issue, add a brief comment so the next developer understands the reasoning:

```python
# deepsource: skip — Beanie async query, await is required
doc = await Model.find_one(Model.field == value)
```

```python
# deepsource: skip — method is overridden in subclasses, must stay bound
def compute_default(self) -> str:
    return "default"
```

## Checklist

1. **Identified** the DeepSource issue type.
2. **Checked** against known false positives above.
3. **Grepped** for all call sites before applying any structural change (especially `staticmethod` conversions).
4. **Preserved** all Beanie async patterns unchanged.
5. **Ran tests** after the fix.
6. **Left a suppression comment** if the issue was a false positive.

## Useful references

- `.agents/skills/issue-fixer/SKILL.md` — general bug-fixing methodology.
- `.agents/skills/code-review/SKILL.md` — pre-submit review checklist.
- `sophie_bot/db/models/` — Beanie model definitions for reference on async patterns.