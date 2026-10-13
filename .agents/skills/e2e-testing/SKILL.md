---
name: e2e-testing
description: Use when writing or updating aiogram end-to-end tests for handlers and bot workflows.
---

# End-to-End Testing

## Principles

- Every new bot handler requires E2E test coverage.
- Only external services (Telegram API, MongoDB, Redis) are mocked. Middlewares, permission checks, filters, and Beanie operations run for real.
- The database is cleared before every test.

## Fixtures & Test State

- Entry point: `test_client`.
- Never mock internal permission functions (`check_user_admin_permissions`). Build real state using `create_test_user_and_group(...)` and `grant_admin(...)` / `grant_bot_admin(...)`.
- Use `extra_router` for test-scoped routes; it detaches automatically.

## Assertions

1. **Telegram API Calls:** Verify via `test_client.capture.get_by_type(RequestType.<TYPE>)` and inspect call arguments.
2. **Database State:** Query Beanie models to assert persistence side effects.
3. **Negative Paths:** Assert unauthorized callers, invalid parameters, and rate-limit drops.
