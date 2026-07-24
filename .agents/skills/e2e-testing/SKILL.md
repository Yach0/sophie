---
name: e2e-testing
description: Use this skill when writing or updating aiogram end-to-end tests, especially for new handlers and user-facing bot flows.
---

# End-to-end testing

Use this skill for tests under `tests/e2e/`.

## When to use it

- Every new bot handler needs an E2E test.
- Update E2E tests when a handler’s user-visible behavior, permissions, or persistence behavior changes.

## Test environment

- `tests/e2e/conftest.py` provides the main fixtures; `test_client` is the entry point.
- Only the world outside the bot is mocked (MongoDB, Redis, Telegram). Handlers, middlewares,
  permission checks, and persistence all run for real.
- The database is empty at the start of every test (autouse `clean_db`), so do not hand-pick
  "globally unique" IDs to dodge collisions.

## Building state — do not patch permission checks

- Use `tests/e2e/helpers.py`:
  - `create_test_user_and_group(test_client, ...)` registers a user + group and returns models.
  - `grant_admin(chat_tid, user_tid, ...)` / `grant_bot_admin(chat_tid, ...)` write real
    `ChatAdminModel` state. Admin-gated handlers then pass without patching
    `check_user_admin_permissions` or `is_user_admin` — those read this state.
  - `next_user_id()` / `next_group_id()` allocate collision-free IDs.
- Need a test-only handler? Use the `extra_router` fixture; it detaches on teardown.

## Test shape

- Prefer `test_<handler>_<scenario>` naming.
- Exercise the real user flow: send the command/interaction, then verify.
- Keep each test independent and explicit about the scenario it covers.

## What to assert

- **Captured Telegram calls** first: `test_client.capture.get_by_type(RequestType.X)` with the
  expected params. This proves the bot took the action, rather than trusting a mocked internal.
- **Database state**: read back the model the handler should have written or left untouched.
- **Reply text**: a secondary check — keep it, but don't rely on it alone.
- Cover negative cases: invalid input, missing permissions (a user you did *not* `grant_admin`).

## Running tests

Use the existing commands:

```bash
TESTING=1 uv run pytest tests/e2e/ -v
TESTING=1 uv run pytest tests/e2e/test_<module>.py -v
TESTING=1 uv run pytest tests/ -v
```

## Useful references

- `tests/e2e/`
- `tests/e2e/conftest.py`
- `tests/utils/mongo_mock.py`