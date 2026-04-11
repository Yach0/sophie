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

- `tests/e2e/conftest.py` provides the main fixtures.
- `test_client` is the primary entry point for user interaction simulation.
- The E2E environment uses mocked MongoDB, Redis, and Telegram APIs.

## Test shape

- Prefer `test_<handler>_<scenario>` naming.
- Exercise the real user flow: send the command or interaction, inspect the bot reply, and verify the database state when relevant.
- Keep each test independent and explicit about the scenario it covers.

## What to assert

- The bot responded.
- The response text or structure matches the intended outcome.
- Relevant database documents were created, updated, or left untouched as expected.
- Important negative cases are covered, such as invalid input or missing permissions.

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