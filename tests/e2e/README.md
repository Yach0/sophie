# End-to-End Tests for Sophie Bot

This directory contains end-to-end tests for Sophie Bot using [aiogram-test-framework](https://github.com/sgavka/aiogram-test-framework).

## Overview

These tests simulate real user interactions with the bot in a fully mocked environment:

- **MongoDB**: Mocked using [mongomock](https://github.com/mongomock/mongomock) with a custom async wrapper
- **Redis**: Mocked using [fakeredis](https://github.com/cunla/fakeredis)
- **Telegram API**: Mocked using aiogram-test-framework's MockBot

## Running Tests

```bash
# Run only e2e tests
TESTING=1 uv run pytest tests/e2e/ -v

# Run all tests including e2e
TESTING=1 uv run pytest tests/ -v
```

## Architecture

### Mock MongoDB (`tests/utils/mongo_mock.py`)

Since mongomock doesn't natively support PyMongo's async interface (AsyncMongoClient), we use a workaround from [mongomock issue #916](https://github.com/mongomock/mongomock/issues/916):

- `AsyncMongoMockClient`: Wraps mongomock's synchronous client
- `AsyncDatabaseMock`: Async wrapper for database operations
- `AsyncCollectionMock`: Async wrapper for collection operations
- `AsyncCursorMock`: Async wrapper for cursor operations

These wrappers run synchronous mongomock operations in an executor to provide async compatibility for Beanie 2.0.

### Test Fixtures (`tests/e2e/conftest.py`)

- `mock_mongo`: Provides the mocked MongoDB client
- `db_init`: Initializes Beanie with all models using the mocked database
- `test_dispatcher`: Creates a Dispatcher with all modules and middlewares loaded
- `test_client`: Provides an aiogram-test-framework TestClient for simulating user interactions

### Example Test

```python
@pytest.mark.asyncio
async def test_start_command_creates_chat(test_client: TestClient) -> None:
    # Create a mock user
    user_id = 123456789
    user = test_client.create_user(user_id=user_id, first_name="Test", username="testuser")

    # Send /start command
    await user.send_command("start")

    # Verify the bot responded
    last_message = user.get_last_message()
    assert last_message is not None
    assert "Sophie" in last_message.text

    # Verify database state
    chat = await ChatModel.find_one(ChatModel.tid == user_id)
    assert chat is not None
```

## CI Integration

E2E tests run in GitLab CI in a separate stage (`e2e`) that:
- Runs in parallel with the build stage
- Doesn't block deployment (uses `allow_failure: true`)
- Runs on every commit and merge request

See `.gitlab-ci.yml` and `build/e2e-test.yml` for configuration.

## Limitations

- **Group chats**: aiogram-test-framework primarily supports private chat testing. Group chat scenarios would require manual message construction.
- **AI features**: Tests skip AI-related functionality as requested.
- **External services**: Any external API calls need to be mocked separately.

## Adding New Tests

1. Create a new test file in `tests/e2e/`
2. Use the `test_client` fixture to interact with the bot
3. Use `test_client.create_user()` to create test users
4. Use user methods like `send_command()`, `send_message()` to simulate interactions
5. Assert on `user.get_last_message()` or check database state using Beanie models

## Troubleshooting

If you see `AttributeError: tid` or similar Beanie model errors, it means Beanie hasn't been initialized. Make sure:
1. The `db_init` fixture runs before your test
2. The test file imports from `tests.e2e.conftest` (pytest should handle this automatically)
