# End-to-End Tests for Sophie Bot

This directory contains end-to-end tests for Sophie Bot using [aiogram-test-framework](https://github.com/sgavka/aiogram-test-framework).

## Overview

These tests drive real user interactions through the real handlers and middlewares. Only the
world outside the bot is mocked:

- **MongoDB**: [mongomock](https://github.com/mongomock/mongomock) behind a custom async wrapper
- **Redis**: [fakeredis](https://github.com/cunla/fakeredis)
- **Telegram API**: aiogram-test-framework's `MockBot`, which records every outgoing request

The bot's own logic — permission checks, persistence, i18n, argument parsing — runs unmocked.

## Running Tests

```bash
# Run only e2e tests
TESTING=1 uv run pytest tests/e2e/ -v

# Run all tests including e2e
TESTING=1 uv run pytest tests/ -v
```

## Architecture

### One database, reset per test

`pymongo.AsyncMongoClient` is patched to a single process-wide `AsyncMongoMockClient`
(`tests/utils/mongo_mock.py`, wired up in `tests/utils/db_fixture.py`). Beanie is initialised
once against it. The `clean_db` autouse fixture truncates every collection after each test, so
tests start empty and never inherit each other's state — no hand-picked "globally unique" IDs.

### Fixtures (`tests/e2e/conftest.py`)

- `db_init` — Beanie initialised against the mock client (session scope)
- `test_dispatcher` — a `Dispatcher` with all modules and middlewares loaded (session scope)
- `test_client` — an aiogram-test-framework `TestClient`; its `capture` records outgoing requests
- `clean_db` — autouse, empties the DB and FSM Redis after each test
- `extra_router` — attach a test-only router for one test; it is detached on teardown

Because handlers reach the bot and dispatcher through the `sophie_bot.services.bot` runtime
proxies, and the `test_client` fixture points those proxies at the mock, tests never need to
monkeypatch per-module `bot`/`dp` references.

### Helpers (`tests/e2e/helpers.py`)

- `create_test_user_and_group(...)` — allocate IDs, register a user + group, return the models
- `next_user_id()` / `next_group_id()` — collision-free ID allocation
- `grant_admin(chat_tid, user_tid, ...)` / `grant_bot_admin(chat_tid, ...)` — write real
  `ChatAdminModel` state so admin-gated handlers pass without patching the permission check

### What to assert on

1. **Captured Telegram calls** — `test_client.capture.get_by_type(RequestType.BAN_CHAT_MEMBER)`
   and friends. This is how the restriction tests verify the bot actually banned/muted someone,
   instead of mocking the action helper.
2. **Database state** — read back the Beanie model the handler should have written.
3. **Reply text** — a secondary check; keep it, but don't rely on it alone.

### Example Test

```python
async def test_ban_calls_telegram(test_client: TestClient) -> None:
    admin, group, _ = await create_test_user_and_group(test_client)
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    target = next_user_id()

    requests = await test_client.send_command(command="ban", from_user=admin, args=str(target), chat=group)

    banned = test_client.capture.get_by_type(RequestType.BAN_CHAT_MEMBER)
    assert banned and banned[0].params["user_id"] == target
```

## CI Integration

E2E tests run in GitLab CI in the `test-e2e` job (see `build/bot-test.yml`).

## Adding New Tests

1. Create a file in `tests/e2e/`
2. Build state with `create_test_user_and_group` and `grant_admin`, not raw literals
3. Drive the flow with `send_command` / `send_message` / `send_callback`
4. Assert on captured requests and DB state (see "What to assert on")
